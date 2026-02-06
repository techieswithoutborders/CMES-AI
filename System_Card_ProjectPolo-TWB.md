# Overview

## Description:
Project Polo is used to evaluate advanced Retrieval-Augmented Generation (RAG) based chatbot specifically designed to handle Trustworthy AI-related queries and questions in low-resource settings with limited Internet access. The purpose of the assessment is to determine whether Project Polo would be useful as a potential solution to equip health professionals in low-income countries with rapid, context-aware access to trustworthy medical knowledge at the point of care, improving clinical decision-making, diagnostic accuracy, and patient outcomes. 

The following functionalities are evaluated:

End-user:
1. Chat Feature: Allows users to interact with the system through a chat interface.
2. Upvote and Downvote answers: Enables users to rate the quality of answers provided by the system.

Program administrator:
1. Data Ingestion: Support for the ingestion of various file types, including JPEG, PNG, PDF, DOCS, PPT, and MP4 Videos.
2. Dashboard: Displays common queries asked and query statistics.

This system is available for limited evaluation use only.

## License/Terms of Use:
This trial service is governed by the NVIDIA Trustworthy AI Model Evaluation License Agreement.

### Deployment Geography:
Sub-Saharan Africa (The Gambia)

### Release Management:
The system is exposed as .zip package for use/download through Product Information Delivery in accordance with NVIDIA's [terms of service](https://www.nvidia.com/en-us/about-nvidia/privacy-policy/).

## Automation Level:
Assistant

## Use Case:
The users of Project Polo are participants in the CMES initiative from Techies Without Borders, an International Non-Profit Organization. The usage is for Trustworthy AI-related queries and questions, particularly in the medical field, with the chatbot serving as a single point of search execution across multiple document types.

## Known Technical Limitations:
1. Context and Tokens: Limited to retrieving 5 chunks for each query and may miss relevant information if it is spread across multiple documents.

2. Model Dependencies: Requires NVIDIA API access and stable internet connection for model downloads and initialization.

3. Storage Constraints: Uses in-memory session storage (cleared on restart) with ChromaDB for vector storage and no auto-timeout.


## Known Risks:
In rare instances, this system may provide misinformation.  If in doubt, please reach out to the product owner Michael Boone (mboone@nvidia.com).

## Fail Safe In-Place:
Emergency Override

### Release Date:
2/28/2025

### Evaluation and Feedback Date:
Aug 2025

## System Architecture:
The system architecture of Project Polo consists of a FastAPI backend (Port 70) handling API requests and LLM operations, a MongoDB database (Port 7002) for user data and chat history storage, and a Streamlit frontend (Port 7001) providing the web interface for document upload and user interactions.

![Project Polo System Architecture](polo-simplified-arch-twb.png)

The following NIMs are used by this blueprint:
1. Document Processing: nvidia/llama-3.1-nemotron-51b-instruct
2. Embeddings: nvidia/nv-embedqa-e5-v5
3. Reranking: nvidia/nv-rerankqa-mistral-4b-v3
4. Judge Model: mistralai/mixtral-8x22b-instruct-v0.1

## Input:
**Input Type(s):** 
- Any File of Text, Image, Audio, or Video Type (Data Ingestion)
- Text (Chat Query)

**Input Format:** 
- String (Data Ingestion)
- String (Chat Query)

**Input Parameters:** One Dimensional (1D)

**Other Properties Related to Input:** No explicit maximum token limit

## Output:
**Output Type(s):** Text

**Output Format:** String

**Output Parameters:** One Dimensional (1D) for Queries, Answers, Ratings

**Other Properties Related to Output:** Maximum of 1024 tokens per field

## Hardware Compatibility:
**Supported Architectures:** <br>
* NVIDIA Ampere <br> 
* NVIDIA Blackwell <br>  
* NVIDIA Jetson <br>  
* NVIDIA Hopper <br>  
* NVIDIA Lovelace <br>  
* NVIDIA Pascal <br>  
* NVIDIA Turing <br>  
* NVIDIA Volta <br> 

**Operating Systems:** <br>
* Linux <br>
* Windows <br>

## System Version(s)
nv-project-polo-v1.1.0 

## Inference:
**Engine:** N/A <br>
**Test Hardware:** <br>
* Supermicro SYS-1019GP-TT <br>

## Ethical Considerations:
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal model team to ensure this model meets requirements for the relevant industry and use case and addresses unforeseen product misuse.

Please report security vulnerabilities or NVIDIA AI Concerns [here](https://www.nvidia.com/en-us/support/submit-security-vulnerability/).

