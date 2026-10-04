import os
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
#from langchain_community.vectorstores import Chroma
from langchain_chroma import Chroma

# Local Paths
REGULATIONS_DIR = "data/regulations"
CHROMA_PERSIST_DIR = "data/chroma_db"

def initialize_vector_store():
    """
    Parses PDFs in the regulations directory, chunks them, and embeds them into ChromaDB.
    """
    # Initialize the Google Gemini Embedding Model
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    
    # Check if the vector store is already built to avoid redundant processing
    if os.path.exists(CHROMA_PERSIST_DIR) and os.listdir(CHROMA_PERSIST_DIR):
        print("Vector store already exists. Connecting to local ChromaDB...")
        return Chroma(
            persist_directory=CHROMA_PERSIST_DIR, 
            embedding_function=embeddings
        )
    
    print(f"Scanning {REGULATIONS_DIR} for regulatory PDFs...")
    
    # 1. Load PDFs
    pdf_loader = PyPDFDirectoryLoader(REGULATIONS_DIR)
    documents = pdf_loader.load()
    
    if not documents:
        raise FileNotFoundError(f"No PDFs found in {REGULATIONS_DIR}. Please add APRA APG 223 and ASIC RG 209.")
        
    print(f"Loaded {len(documents)} document pages.")

    # 2. Chunk the Documents
    # We use a 1000-character chunk with a 150-character overlap to preserve legal context across page breaks.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ".", " "]
    )
    chunks = text_splitter.split_documents(documents)
    
    # 3. Embed and persist in ChromaDB
    print(f"Embedding {len(chunks)} chunks into ChromaDB. This may take a moment...")
    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_PERSIST_DIR
    )
    
    print("✅ Regulatory vector database built and saved successfully.")
    return vector_store

def retrieve_regulatory_clauses(query: str, k: int = 3) -> str:
    """
    Function to be used by the LangGraph agent to semantically search the regulatory database.
    """
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    vector_store = Chroma(
        persist_directory=CHROMA_PERSIST_DIR, 
        embedding_function=embeddings
    )
    
    # Perform similarity search
    results = vector_store.similarity_search(query, k=k)
    
    context = []
    for i, doc in enumerate(results):
        # Extract filename from the source metadata for citation
        source_file = doc.metadata.get("source", "Unknown Source").split("/")[-1]
        page_num = doc.metadata.get("page", "Unknown Page")
        
        context.append(f"[Citation {i+1} - {source_file}, Page {page_num}]\n{doc.page_content}\n")
        
    return "\n".join(context)

if __name__ == "__main__":
    # Test execution block
    print("--- Initializing Vector Store ---")
    initialize_vector_store()
    
    print("\n--- Testing RAG Retrieval ---")
    test_query = "What are the requirements for verifying living expenses and using the HEM benchmark?"
    print(f"Query: '{test_query}'\n")
    print(retrieve_regulatory_clauses(test_query))
