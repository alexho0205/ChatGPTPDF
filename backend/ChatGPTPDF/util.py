import re
from io import BytesIO
from typing import List, Dict, Any
from langchain import VectorDBQA

from langchain.chains.qa_with_sources import load_qa_with_sources_chain
from langchain.chains.question_answering import load_qa_chain
from langchain.chains import ChatVectorDBChain
from langchain.docstore.document import Document
from langchain.llms import OpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import VectorStore
from langchain.vectorstores.faiss import FAISS
from langchain.vectorstores import Pinecone
from langchain.prompts import PromptTemplate
from langchain.chains.summarize import load_summarize_chain
from openai.error import AuthenticationError
from pypdf import PdfReader
import openai
from langchain.chat_models import ChatOpenAI
# from .embeddings import OpenAIEmbeddings
from langchain.embeddings import OpenAIEmbeddings
import os
import uuid
import dotenv
import pinecone
from application import settings

# loading the .env file
dotenv.load_dotenv()

# 初始化必要的API
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# pinecone.init(api_key=os.getenv("OPENAI_API_KEY"))
openai.api_key = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_API_ENV = os.getenv("PINECONE_API_ENV")

CONDENSE_PROMPT = PromptTemplate.from_template(
    "Given the following conversation and a follow up question, rephrase the follow up question to be a standalone \
    question.\n\n \
    Chat History:\n \
    {chat_history}\n \
    Follow Up Input: {question}\n \
    Standalone question:"
)

QA_PROMPT = PromptTemplate.from_template(
    """You are an AI assistant providing helpful advice. You are given the following extracted parts of a long document 
    and a question. Provide a conversational answer based on the context provided.\n 
    You should only provide hyperlinks that reference the context below. Do NOT make up hyperlinks.\n 
    ALWAYS include a "SOURCES" section in your answer including only the minimal set of sources needed to answer the question. 
    If you are unable to answer the question, simply state that you do not know. Do not attempt to fabricate an answer and leave the SOURCES section empty.
    that are related to the context.\n\n 
    QUESTION: What  is the purpose of ARPA-H?
    =========
    Content: More support for patients and families. \n\nTo get there, I call on Congress to fund ARPA-H, the Advanced Research Projects Agency for Health. \n\nIt’s based on DARPA—the Defense Department project that led to the Internet, GPS, and so much more.  \n\nARPA-H will have a singular purpose—to drive breakthroughs in cancer, Alzheimer’s, diabetes, and more.
    Source: 1-32
    Content: While we’re at it, let’s make sure every American can get the health care they need. \n\nWe’ve already made historic investments in health care. \n\nWe’ve made it easier for Americans to get the care they need, when they need it. \n\nWe’ve made it easier for Americans to get the treatments they need, when they need them. \n\nWe’ve made it easier for Americans to get the medications they need, when they need them.
    Source: 1-33
    Content: The V.A. is pioneering new ways of linking toxic exposures to disease, already helping  veterans get the care they deserve. \n\nWe need to extend that same care to all Americans. \n\nThat’s why I’m calling on Congress to pass legislation that would establish a national registry of toxic exposures, and provide health care and financial assistance to those affected.
    Source: 1-30
    =========
    FINAL ANSWER: The purpose of ARPA-H is to drive breakthroughs in cancer, Alzheimer’s, diabetes, and more.
    SOURCES: 1-32
    ---------
    Question: {question}\n 
    =========\n{context}\n=========\n 
    Answer in Markdown in Chinese:"""
)

## Use a shorter template to reduce the number of tokens in the prompt
template = """Create a final answer to the given questions using the provided document excerpts(in no particular order) as references. ALWAYS include a "SOURCES" section in your answer including only the minimal set of sources needed to answer the question. If you are unable to answer the question, simply state that you do not know. Do not attempt to fabricate an answer and leave the SOURCES section empty.
Answer in Chinese
---------

QUESTION: What  is the purpose of ARPA-H?
=========
Content: More support for patients and families. \n\nTo get there, I call on Congress to fund ARPA-H, the Advanced Research Projects Agency for Health. \n\nIt’s based on DARPA—the Defense Department project that led to the Internet, GPS, and so much more.  \n\nARPA-H will have a singular purpose—to drive breakthroughs in cancer, Alzheimer’s, diabetes, and more.
Source: 1-32
Content: While we’re at it, let’s make sure every American can get the health care they need. \n\nWe’ve already made historic investments in health care. \n\nWe’ve made it easier for Americans to get the care they need, when they need it. \n\nWe’ve made it easier for Americans to get the treatments they need, when they need them. \n\nWe’ve made it easier for Americans to get the medications they need, when they need them.
Source: 1-33
Content: The V.A. is pioneering new ways of linking toxic exposures to disease, already helping  veterans get the care they deserve. \n\nWe need to extend that same care to all Americans. \n\nThat’s why I’m calling on Congress to pass legislation that would establish a national registry of toxic exposures, and provide health care and financial assistance to those affected.
Source: 1-30
=========
FINAL ANSWER: The purpose of ARPA-H is to drive breakthroughs in cancer, Alzheimer’s, diabetes, and more.
SOURCES: 1-32

---------

QUESTION: {question}
=========
{summaries}
=========
FINAL ANSWER:"""

STUFF_PROMPT = PromptTemplate(
    template=template, input_variables=["summaries", "question"]
)

template2 = """Create a final answer to the given questions using the provided document excerpts(in no particular order) as references. 
        ALWAYS include a "SOURCES" section in your answer including only the minimal set of sources needed to answer the question. 
        If you are unable to answer the question, simply state that you do not know. Do not attempt to fabricate an answer and leave the SOURCES section empty.
Answer in Chinese
---------
QUESTION: {question}
=========
{context}
=========
FINAL ANSWER:"""

STUFF_PROMPT2 = PromptTemplate(
    template=template2, input_variables=["context", "question"]
)

template3 = """Create a final answer to the given questions using the provided document excerpts(in no particular order) as references. 
              If you are unable to answer the question, simply state that you do not know. Do not attempt to fabricate an answer .
Answer in Chinese
---------
QUESTION: {question}
=========
{context}
=========
FINAL ANSWER:"""

STUFF_PROMPT3 = PromptTemplate(
    template=template3, input_variables=["context", "question"]
)

index = None


def parse_pdf(filePath) -> List[str]:
    pdf_file = open(filePath, "rb")
    pdf = PdfReader(pdf_file)
    output = []
    for page in pdf.pages:
        text = page.extract_text()
        # Merge hyphenated words
        text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
        # Fix newlines in the middle of sentences
        text = re.sub(r"(?<!\n\s)\n(?!\s\n)", " ", text.strip())
        # Remove multiple newlines
        text = re.sub(r"\n\s*\n", "\n\n", text)

        output.append(text)

    return output


def text_to_docs(text: str | List[str]) -> List[Document]:
    """Converts a string or list of strings to a list of Documents
    with metadata."""
    if isinstance(text, str):
        # Take a single string as one page
        text = [text]
    page_docs = [Document(page_content=page) for page in text]

    # Add page numbers as metadata
    for i, doc in enumerate(page_docs):
        doc.metadata["page"] = i + 1

    # Split pages into chunks
    doc_chunks = []

    for doc in page_docs:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            separators=["\n\n", "\n", ".", "!", "?", ",", " ", ""],
            chunk_overlap=0,
        )
        chunks = text_splitter.split_text(doc.page_content)
        for i, chunk in enumerate(chunks):
            doc = Document(
                page_content=chunk, metadata={"page": doc.metadata["page"], "chunk": i}
            )
            # Add sources a metadata
            doc.metadata["source"] = f"{doc.metadata['page']}-{doc.metadata['chunk']}"
            doc_chunks.append(doc)

        # 使用切片操作获取前10个元素
        first_10 = doc_chunks[:10]
        # print(first_10)

    return doc_chunks


def embed_docs(docs: List[Document], uuid: str | None) -> VectorStore:
    """Embeds a list of Documents and returns a FAISS index"""

    if uuid is None:
        my_uuid = uuid.uuid4()
        uuid = str(my_uuid)

    # Create a FAISS index
    if not OPENAI_API_KEY:
        raise AuthenticationError(
            "Enter your OpenAI API key in the sidebar. You can get a key at"
            " https://platform.openai.com/account/api-keys."
        )
    else:
        # Embed the chunks
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)  # type: ignore
        index = FAISS.from_documents(docs, embeddings)
        # print(index)
        # Save the index
        index_path = f"{settings.INDEX_ROOT}/{uuid}"
        index.save_local(folder_path=index_path)

        return index


def pinecone_embed_docs(docs: List[Document], uuid: str | None = None) -> str:
    """Embeds a list of Documents and returns the ID of the Pinecone index"""
    if uuid is None:
        my_uuid = uuid.uuid4()
        uuid = str(my_uuid)

    # Initialize OpenAI Embedding
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)

    # Initialize Pinecone connection
    
    pinecone.init(
        api_key=PINECONE_API_KEY,  # find at app.pinecone.io
        environment=PINECONE_API_ENV,  # next to api key in console
    )

    # Create the index if it does not exist
    index_name = f"index-{uuid}"
  
    try:
       index = pinecone.Index(index_name=index_name)
    except Exception as error:
       pinecone.create_index(index_name, dimension=1538)

    index = Pinecone.from_documents(docs, OpenAIEmbeddings(), index_name=index_name)
    return index

def get_pinecone_embedding(uuid: str) -> pinecone.Index:
    """Get embeddings for a list of Documents from a Pinecone index"""
    try:
        pinecone.init(api_key=PINECONE_API_KEY, environment=PINECONE_API_ENV)
        index_name = f"index-{uuid}"
       
        try:
            index = pinecone.Index(index_name=index_name)
        except Exception as error:
            pinecone.create_index(index_name, dimension=1024)

        embeddings = OpenAIEmbeddings()
        vectorstore = Pinecone(index, embeddings.embed_query, "text")

        return vectorstore
    except Exception as e:
        print(e)
        return None


def get_store_embedding(uuid: str) -> List[Dict[str, Any]]:
    """Get embeddings for a list of Documents from a FAISS index"""
    try:
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        index_path = f"{settings.INDEX_ROOT}/{uuid}"
        index = FAISS.load_local(index_path, embeddings)
        # print(index)
        return index
    except Exception as e:
        print(e)
        return None


def search_docs(index: VectorStore, query: str) -> List[Document]:
    """Searches a FAISS index for similar chunks to the query
    and returns a list of Documents."""

    # Search for similar chunks
    docs = index.similarity_search(query, k=5)
    # print(docs)
    return docs


def get_answer_pinecone(index: VectorStore, history: list[dict[str, str]], query: str) -> Dict[str, Any]:
    """Gets an answer to a question from a list of Documents."""

    # Get the answer

    chain = ChatVectorDBChain.from_llm(
        llm=ChatOpenAI(
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            max_tokens=512,
            model_name="gpt-3.5-turbo",
        ),
        vectorstore=index,
        condense_question_prompt=CONDENSE_PROMPT,
        qa_prompt=STUFF_PROMPT2,
        chain_type="stuff",
        # return_source_documents=True,
    )

    # Cohere doesn't work very well as of now.
    # chain = load_qa_with_sources_chain(
    #     Cohere(temperature=0), chain_type="stuff", prompt=STUFF_PROMPT  # type: ignore
    # )
    answer = chain(
        {"chat_history": history, "question": query}, return_only_outputs=True
    )

    return answer

def get_answer_summary(docs: List[Document], query: str) -> Dict[str, Any]:
    model = load_summarize_chain(llm=ChatOpenAI(
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            max_tokens=500,
            model_name="gpt-3.5-turbo",
        ), chain_type="map_reduce")
    model.run(docs)
    return model

def get_answer_qa(docs: List[Document], query: str) -> Dict[str, Any]:
    """Gets an answer to a question from a list of Documents."""

    # Get the answer

    # chain = load_qa_with_sources_chain(
    #     OpenAI(
    #         temperature=0, openai_api_key=OPENAI_API_KEY
    #     ),  # type: ignore
    #     chain_type="stuff",
    #     prompt=STUFF_PROMPT2,
    # )
    chain = load_qa_chain(
        ChatOpenAI(
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            max_tokens=512,
            model_name="gpt-3.5-turbo",
        ),  # type: ignore
        chain_type="stuff",
        prompt=STUFF_PROMPT3,
    )

    # Cohere doesn't work very well as of now.
    # chain = load_qa_with_sources_chain(
    #     Cohere(temperature=0), chain_type="stuff", prompt=STUFF_PROMPT  # type: ignore
    # )

    answer = chain({"input_documents": docs, "question": query})
    #
    return answer


def get_answer(
    index: VectorStore, history: list[dict[str, str]], query: str
) -> Dict[str, Any]:
    """Gets an answer to a question from a list of Documents."""

    # Get the answer

    chain = ChatVectorDBChain.from_llm(
        llm=ChatOpenAI(
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            max_tokens=500,
            model_name="gpt-3.5-turbo",
        ),
        vectorstore=index,
        condense_question_prompt=CONDENSE_PROMPT,
        qa_prompt=STUFF_PROMPT2,
        chain_type="stuff",
        # return_source_documents=True,
    )

    # Cohere doesn't work very well as of now.
    # chain = load_qa_with_sources_chain(
    #     Cohere(temperature=0), chain_type="stuff", prompt=STUFF_PROMPT  # type: ignore
    # )

    new_history = []
    humanMessage = ""
    aiMessage = ""   
    for message in history:
        if message.type == "human" and humanMessage == "":
            humanMessage = message.content
        elif message.type == "ai" and aiMessage == "":
            aiMessage = message.content
        if humanMessage != "" and aiMessage != "":
            new_message = { "Human":humanMessage,
                            "AI":aiMessage }
            new_history.append(new_message)
            humanMessage = ""
            aiMessage = ""

    answer = chain(
        {"chat_history": new_history, "question": query}, return_only_outputs=True
    )

    return answer


def get_sources(answer: Dict[str, Any], docs: List[Document]) -> List[Document]:
    """Gets the source documents for an answer."""
    # print("answer: ", answer)
    # print("docs: ", docs)
    # Get sources for the answer
    sources = answer.get("source_documents", [])
    if sources: 
        sources = answer["source_documents"]

        source_docs = []
        for doc in sources:
            if doc.metadata["source"]:
                source_docs.append(doc.metadata["source"])

        return source_docs
    else:
        return []

def get_sources_qa(answer: Dict[str, Any], docs: List[Document]) -> List[Document]:
    """Gets the source documents for an answer."""

    # Get sources for the answer
    # source_keys = [s for s in answer["output_text"].split("SOURCES: ")[-1].split(", ")]
    print("get_sources_qa")
    source_docs = []
    source_docs = []
    for doc in docs:
        if doc.metadata["source"]:
            source_docs.append(doc.metadata["source"])

    return source_docs
