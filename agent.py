import os
import google.generativeai as genai
from dotenv import load_dotenv
import traceback
from duckduckgo_search import DDGS
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup
import re

# Load .env variables
load_dotenv()

# Get env variables
api_key = os.getenv("GEMINI_API_KEY", "AIzaSyBRAKfYEnbImVZOEESX7KuIA8Op5mWI9js")
model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
BAILII_SITE_QUERY = os.getenv("BAILII_SITE_QUERY", "site:bailii.org")
LEGISLATION_API_QUERY = os.getenv("LEGISLATION_API_QUERY", "site:legislation.gov.uk")

# Configure Gemini API + safer, more focused generation settings
genai.configure(api_key=api_key)
try:
    # Use the user's preferred model; default to a broadly available fast model
    preferred_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    model = genai.GenerativeModel(preferred_model)
except Exception:
    model = genai.GenerativeModel(model_name)

# Example: build legislation.gov.uk query
def build_legislation_query(user_input: str) -> str:
    return f"{user_input} {LEGISLATION_API_QUERY}"
def build_bailii_query(user_input: str) -> str:
    return f"{user_input} {BAILII_SITE_QUERY}"
def search_legislation_references(query: str, max_results: int = 5):
    """Return top legislation references (title, url, snippet) using DuckDuckGo.
    Filters to legislation.gov.uk and .gov.uk domains.
    """
    try:
        refs = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, region="uk-en", safesearch="moderate", max_results=max_results):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                # keep UK legislation/government sources primarily
                host = url.lower()
                if ("legislation.gov.uk" in host) or host.endswith(".gov.uk") or "://www.legislation.gov.uk" in host:
                    refs.append({
                        "title": (r.get("title") or r.get("heading") or "Legislation reference"),
                        "url": url,
                        "snippet": r.get("body") or r.get("snippet") or ""
                    })
                if len(refs) >= max_results:
                    break
        return refs
    except Exception as e:
        print(f"⚠️ Legislation search failed: {e}")
        return []

def search_bailii_references(query: str, max_results: int = 5):
    """Return top BAILII case law references (title, url, snippet)."""
    try:
        refs = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, region="uk-en", safesearch="moderate", max_results=max_results * 2):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                host = (url or "").lower()
                if "bailii.org" in host:
                    refs.append({
                        "title": (r.get("title") or r.get("heading") or "BAILII case"),
                        "url": url,
                        "snippet": r.get("body") or r.get("snippet") or ""
                    })
                if len(refs) >= max_results:
                    break
        return refs
    except Exception as e:
        print(f"⚠️ BAILII search failed: {e}")
        return []

def bailii_lucy_search(query: str, max_results: int = 5):
    """Query BAILII Lucy search and parse case results with titles and links.
    Example endpoint: https://www.bailii.org/cgi-bin/lucy_search_1.cgi?q=illegal+workers
    """
    try:
        params = {"q": query}
        url = f"https://www.bailii.org/cgi-bin/lucy_search_1.cgi?{urlencode(params)}"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for a in soup.select("a"):
            href = a.get("href") or ""
            text = (a.get_text() or "").strip()
            if not href or not text:
                continue
            # Heuristic: judgment links usually under /uk/cases/...
            if href.startswith("/"):
                full = f"https://www.bailii.org{href}"
            else:
                full = href
            if "bailii.org" in full and any(part in full for part in ["/uk/cases/", "/ie/cases/", "/uk/cmu/", "/uk/other/"]):
                results.append({"title": text, "url": full})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print(f"⚠️ BAILII Lucy parse failed: {e}")
        return []

def fetch_bailii_judgment_summary(url: str) -> str:
    """Fetch a BAILII judgment page and attempt to extract a brief outcome summary.
    This is heuristic: we look for paragraphs containing 'Held', 'Conclusion', 'Decision'.
    """
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        text_blocks = [p.get_text(" ", strip=True) for p in soup.select("p, div")]
        candidates = []
        for t in text_blocks:
            tl = t.lower()
            if any(k in tl for k in ["held", "conclusion", "decision", "disposition", "order"]):
                candidates.append(t)
        if candidates:
            best = candidates[0]
            if len(best) > 400:
                best = best[:400].rsplit(" ", 1)[0] + "…"
            return best
        # fallback: first couple paragraphs
        if text_blocks:
            join = " ".join(text_blocks[:2])
            return join[:400] + ("…" if len(join) > 400 else "")
        return ""
    except Exception as e:
        print(f"⚠️ BAILII judgment fetch failed: {e}")
        return ""

# ---------------- Similar cases helpers ---------------- #
CASE_NAME_REGEXES = [
    re.compile(r"\b(?:R|Regina)\s+v\.?\s+([A-Z][\w'\.-]+(?:\s+[A-Z][\w'\.-]+)*)", re.IGNORECASE),
    re.compile(r"\b([A-Z][\w'\.-]+(?:\s+[A-Z][\w'\.-]+)*)\s+v\.?\s+([A-Z][\w'\.-]+(?:\s+[A-Z][\w'\.-]+)*)", re.IGNORECASE),
]

def extract_case_names(text: str) -> list[str]:
    names: list[str] = []
    if not text:
        return names
    for rx in CASE_NAME_REGEXES:
        for m in rx.finditer(text):
            if rx is CASE_NAME_REGEXES[0]:  # Regina pattern returns defendant only; rebuild full name
                full = f"R v {m.group(1)}"
                names.append(full)
            else:
                full = f"{m.group(1)} v {m.group(2)}"
                names.append(full)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for n in names:
        key = n.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(n.strip())
    return unique

def find_similar_cases(primary_case: str, context: str = "", max_results: int = 5) -> list[dict]:
    # Build keyword query by removing generic prefixes
    base = re.sub(r"^(?:R|Regina)\s+v\.?\s+", "", primary_case, flags=re.IGNORECASE).strip()
    query_terms = base
    # Try a few variants
    results: list[dict] = []
    tried = set()
    def add_candidates(q: str):
        nonlocal results
        key = q.lower()
        if key in tried:
            return
        tried.add(key)
        refs = bailii_lucy_search(q, max_results=max(0, max_results - len(results)))
        for r in refs:
            if len(results) >= max_results:
                break
            r = dict(r)
            r["snippet"] = r.get("snippet") or fetch_bailii_judgment_summary(r.get("url", ""))
            results.append(r)

    # 1) Exact
    add_candidates(primary_case)
    # 2) Defendant only
    if query_terms:
        add_candidates(query_terms)
    # 3) Context keywords (if any meaningful words present)
    if context:
        # Pull a few nouns/keywords heuristically
        words = re.findall(r"[A-Za-z]{5,}", context)
        if words:
            add_candidates(f"{query_terms} {' '.join(words[:3])}")
    return results[:max_results]


# ===================== FUNCTIONS ===================== #

def _fallback_structured_summary(text: str) -> str:
    # Very simple heuristic fallback if Gemini is unavailable
    try:
        # Take first ~3 sentences or 400 chars for summary
        snippet = (text or "").strip()
        if not snippet:
            return (
                "1. Summary\nNo document content provided.\n\n"
                "2. Missing Clauses\nN/A\n\n"
                "3. Risky Language\nN/A\n\n"
                "4. Suggestions\nPlease upload a readable document."
            )
        sentences = [s.strip() for s in snippet.replace("\n", " ").split('.') if s.strip()]
        summary = '. '.join(sentences[:3])
        if len(summary) > 400:
            summary = summary[:400].rsplit(' ', 1)[0] + '…'
        return (
            f"1. Summary{chr(10)}{summary}.{chr(10)}{chr(10)}"
            "2. Missing Clauses\nNot specified in the document.\n\n"
            "3. Risky Language\nNone identified from the provided text.\n\n"
            "4. Suggestions\nClarify critical terms and add missing operational details."
        )
    except Exception:
        return (
            "1. Summary\nAnalysis failed.\n\n"
            "2. Missing Clauses\nN/A\n\n"
            "3. Risky Language\nN/A\n\n"
            "4. Suggestions\nPlease try again later."
        )


def analyze_document(text):
    """Analyze a legal document and return structured insights using Gemini.
    This version mirrors the previously working prompt/style.
    """
    if not text or not text.strip():
        return (
            "1. Summary\nNo document content provided.\n\n"
            "2. Missing Clauses\nN/A\n\n"
            "3. Risky Language\nN/A\n\n"
            "4. Suggestions\nPlease upload a readable document."
        )

    prompt = f"""
You are a legal document assistant.

Analyze the following legal document and provide:
1. A summary
2. Any missing clauses
3. Risky or unclear language
4. Suggestions for improvement

Document:
{text}
"""
    try:
        # Ensure API key present
        if not api_key:
            print("❌ Missing GEMINI_API_KEY; using fallback summarizer.")
            return _fallback_structured_summary(text)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini error in analyze_document: {e}")
        # Fallback to heuristic summary so UI is not empty
        return _fallback_structured_summary(text)

def get_answer_from_gemini(question: str, document: str) -> str:
    """Get an answer from Gemini using comprehensive case and document context."""
    greetings = ["hi", "hello", "hey", "salam", "السلام علیکم"]
    if question.lower().strip() in greetings:
        return "👋 Hello! I'm your legal case assistant. I can help you with questions about your cases, documents, and legal matters. How can I assist you today?"

    # Build legislation.gov.uk query for UK legal references
    legislation_query = build_legislation_query(question)
    bailii_query = build_bailii_query(question)
    legislation_refs = search_legislation_references(legislation_query, max_results=5)
    # Combine DuckDuckGo site:bailii.org with direct Lucy search for better coverage
    bailii_refs = search_bailii_references(bailii_query, max_results=3)
    bailii_refs += bailii_lucy_search(question, max_results=5 - len(bailii_refs))
    # Enrich bailii results with brief outcome summaries
    enriched_bailii = []
    for r in bailii_refs[:5]:
        summary = fetch_bailii_judgment_summary(r.get("url", "")) if r.get("url") else ""
        enriched_bailii.append({**r, "snippet": r.get("snippet") or summary})
    bailii_refs = enriched_bailii
    refs_legislation_block = chr(10).join([
        f"- {ref['title']}: {ref['url']}" + (f"{chr(10)}  Note: {ref['snippet']}" if ref.get('snippet') else "")
        for ref in legislation_refs
    ]) if legislation_refs else "- No legislation sources found for this query."
    refs_bailii_block = chr(10).join([
        f"- {ref['title']}: {ref['url']}" + (f"{chr(10)}  Outcome: {ref['snippet']}" if ref.get('snippet') else "")
        for ref in bailii_refs
    ]) if bailii_refs else "- No BAILII cases found for this query."
    
    # Check if we have substantial document content
    has_document_content = document and len(document.strip()) > 100
    
    # Enhanced prompt based on available content
    # Try to extract a primary case name to power similar-cases search
    extracted_cases = extract_case_names(f"{question}{chr(10)}{document if has_document_content else ''}")
    primary_case = extracted_cases[0] if extracted_cases else None
    similar_cases = find_similar_cases(primary_case, context=document, max_results=5) if primary_case else []

    if has_document_content:
        prompt = f"""
You are a professional legal case assistant with access to comprehensive case information and uploaded legal documents.

CONTEXT - Here is the available case and document information:
{document}

USER QUESTION: {question}

INSTRUCTIONS:
1. **ANALYZE THE UPLOADED DOCUMENT**: Carefully examine the uploaded document content above
2. **USE DOCUMENT SPECIFICS**: Reference specific clauses, terms, and content from the uploaded document
3. **CASE METADATA**: Use case information (dates, status, customer details) when relevant
4. **LEGAL ANALYSIS**: For legal questions, analyze the document content and provide insights
5. **UK LEGISLATION**: Consider UK legal framework (search hint: "{legislation_query}"). Where relevant, compare the document to the statutes below and cite them inline.
6. **CASE LAW (BAILII)**: Consider UK case law (search hint: "{bailii_query}"). Where relevant, compare the document to the case law below and cite it inline.
7. **SIMILAR CASES (BAILII)**: If a case name is detected (e.g., "{primary_case or 'N/A'}"), list similar UK cases with brief outcomes.
8. **DOCUMENT REVIEW**: If asked about document review, analyze the uploaded content for:
   - Missing important clauses
   - Potential legal risks
   - Areas for improvement
   - Compliance with UK law
7. **SPECIFIC REFERENCES**: Quote or reference specific parts of the uploaded document
8. **PROFESSIONAL ADVICE**: Provide practical, actionable legal insights
9. **DOCUMENT CONTENT FOCUS**: Always prioritize the uploaded document content in your response
10. **QUOTE SPECIFIC SECTIONS**: When possible, quote or reference specific parts of the document

LEGISLATION CANDIDATE SOURCES:
{refs_legislation_block}

BAILII CASE LAW CANDIDATES:
{refs_bailii_block}

SIMILAR CASES DETECTED:
{chr(10).join([f"- {c['title']}: {c['url']}" + (f"{chr(10)}  Outcome: {c['snippet']}" if c.get('snippet') else '') for c in similar_cases]) if similar_cases else "- None auto-detected"}

Please provide a comprehensive response that specifically references and analyzes the uploaded document content. Make sure your answer is directly related to what's in the uploaded document.
"""
    else:
        # Fallback prompt when no substantial document content is available
        prompt = f"""
You are a professional legal case assistant with access to case information.

CONTEXT - Here is the available case information:
{document}

USER QUESTION: {question}

INSTRUCTIONS:
1. Use the available case information to answer questions
2. For legal advice questions, consider UK legislation (search hint: "{legislation_query}"). Compare relevant points to the statutes below when appropriate, and cite them. Also consider UK case law (search hint: "{bailii_query}") and cite relevant cases.
3. Be professional, clear, and concise
4. If you don't have enough information, suggest uploading relevant documents
5. Focus on case management and general legal guidance

LEGISLATION CANDIDATE SOURCES:
{refs_legislation_block}

BAILII CASE LAW CANDIDATES:
{refs_bailii_block}

Please provide a helpful response based on the available information.
"""
    try:
        response = model.generate_content(prompt)
        answer = response.text.strip()
        # Append references at the end for user transparency
        if legislation_refs or bailii_refs:
            refs_list = []
            if legislation_refs:
                refs_list.append("Legislation:")
                refs_list.extend([f"- {r['title']}: {r['url']}" for r in legislation_refs])
            if bailii_refs:
                refs_list.append("Case law (BAILII):")
                refs_list.extend([f"- {r['title']}: {r['url']}" for r in bailii_refs])
            answer = f"{answer}{chr(10)}{chr(10)}References:{chr(10)}{chr(10).join(refs_list)}"
        return answer
    except Exception as e:
        print(f"❌ Gemini error in get_answer_from_gemini: {e}")
        
        # Enhanced fallback responses based on the question type
        question_lower = question.lower()
        
        # Handle common questions with helpful responses
        if any(word in question_lower for word in ["case", "cases"]):
            if document and document.strip():
                return "📋 I can see your case information in the database. Based on what I have:" + chr(10) + chr(10) + document[:500] + "..." + chr(10) + chr(10) + "What specific information about your cases would you like to know?"
            else:
                return "📋 I don't see any cases in your database yet. To get started:" + chr(10) + chr(10) + "1. Click the '+ Add New Case' button" + chr(10) + "2. Fill in the case details" + chr(10) + "3. Upload relevant documents" + chr(10) + "4. Then I can help you with case-specific questions!"
        
        elif any(word in question_lower for word in ["document", "documents", "file", "files"]):
            if document and document.strip():
                # Check if this is substantial document content
                if len(document.strip()) > 100:
                    return "📄 I can see your uploaded document content! Here's what I found:" + chr(10) + chr(10) + document[:500] + "..." + chr(10) + chr(10) + "I can help you:" + chr(10) + "• Analyze specific clauses and terms" + chr(10) + "• Identify potential legal issues" + chr(10) + "• Review compliance with UK law" + chr(10) + "• Suggest improvements" + chr(10) + chr(10) + "What would you like me to analyze in your document?"
                else:
                    return "📄 I can see some case information. To get better document analysis:" + chr(10) + chr(10) + "1. Upload legal documents in the case form" + chr(10) + "2. Then I can provide detailed document review and legal insights" + chr(10) + "3. I'll analyze specific clauses, risks, and compliance issues"
            else:
                return "📄 I don't see any documents uploaded yet. To get started:" + chr(10) + chr(10) + "1. Upload case documents using the file upload areas" + chr(10) + "2. Once documents are uploaded, I can help analyze them" + chr(10) + "3. I can also help with document drafting and review!"
        
        elif any(word in question_lower for word in ["legal", "law", "advice", "help"]):
            return "⚖️ I'm here to help with legal case management! I can assist you with:" + chr(10) + chr(10) + "• Case tracking and status updates" + chr(10) + "• Document analysis and review" + chr(10) + "• Legal document drafting" + chr(10) + "• Case timeline management" + chr(10) + "• Customer information tracking" + chr(10) + chr(10) + "What specific legal assistance do you need today?"
        
        elif any(word in question_lower for word in ["customer", "client"]):
            if document and document.strip():
                return "👥 I can see customer information in your cases. Here's what I found:" + chr(10) + chr(10) + document[:500] + "..." + chr(10) + chr(10) + "What specific customer information do you need?"
            else:
                return "👥 I don't see any customer information yet. To get started:" + chr(10) + chr(10) + "1. Add customers using the '+ Add New Customer' button" + chr(10) + "2. Link customers to cases" + chr(10) + "3. Then I can help you with customer-related questions!"
        
        # Default helpful response
        else:
            return "🤖 I'm your legal case assistant! I can help you with:" + chr(10) + chr(10) + "• **Case Management**: Track case status, deadlines, and progress" + chr(10) + "• **Document Handling**: Upload, analyze, and review legal documents" + chr(10) + "• **Customer Information**: Manage client details and communications" + chr(10) + "• **Legal Support**: Get help with document drafting and case analysis" + chr(10) + chr(10) + "To get started, try:" + chr(10) + "1. Adding a new case or customer" + chr(10) + "2. Uploading some documents" + chr(10) + "3. Asking me specific questions about your legal work" + chr(10) + chr(10) + "What would you like to do first?"

def call_gemini_api(prompt: str) -> str:
    """Generic call to Gemini with any prompt."""
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini error in call_gemini_api: {e}")
        return "❌ Sorry, Gemini couldn't process the input."

def analyze_uploaded_document_content(document_text: str, question: str) -> str:
    """Analyze uploaded document content and answer questions specifically from that content."""
    if not document_text or len(document_text.strip()) < 50:
        return "❌ The uploaded document doesn't contain enough text to analyze. Please ensure the document has readable content."
    
    prompt = f"""
You are a legal document analysis expert. You have been given an uploaded document and a specific question about it.

UPLOADED DOCUMENT CONTENT:
{document_text}

USER QUESTION: {question}

INSTRUCTIONS:
1. **FOCUS ONLY ON THE UPLOADED DOCUMENT**: Your answer must be based solely on the content provided above
2. **REFERENCE SPECIFIC CONTENT**: Quote or reference specific parts of the document when answering
3. **ANALYZE THE DOCUMENT**: Look for relevant information that answers the user's question
4. **BE SPECIFIC**: Don't give generic advice - give specific insights from the document content
5. **IDENTIFY KEY POINTS**: Highlight important clauses, terms, dates, or information from the document
6. **LEGAL INSIGHTS**: Provide legal analysis based on what's actually in the document

IMPORTANT: If the question cannot be answered from the uploaded document content, say so clearly and explain what information is missing.

Please provide a detailed answer that directly references the uploaded document content.
"""
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini error in analyze_uploaded_document_content: {e}")
        return f"❌ Sorry, I couldn't analyze the document content due to an error: {str(e)}"

def generate_document_drafts(text):
    """Generate document drafts using Gemini."""
    try:
        print("📄 Drafting started")

        if not text.strip():
            return {
                "cover_letter": "❌ Empty document text.",
                "form_data": "❌ No readable content.",
                "supporting": "❌ No readable content.",
                "debug": "Text extracted from PDF is empty."
            }

        prompt1 = f"Generate a professional cover letter:{chr(10)}{text[:3000]}"
        prompt2 = f"Extract important form-style data:{chr(10)}{text[:3000]}"
        prompt3 = f"Create a strong supporting statement:{chr(10)}{text[:3000]}"

        print("⏳ Sending prompts to Gemini...")

        response1 = model.generate_content(prompt1)
        response2 = model.generate_content(prompt2)
        response3 = model.generate_content(prompt3)

        return {
            "cover_letter": getattr(response1, 'text', '❌ No cover letter text.'),
            "form_data": getattr(response2, 'text', '❌ No form data text.'),
            "supporting": getattr(response3, 'text', '❌ No supporting statement text.')
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "cover_letter": "❌ Error generating cover letter.",
            "form_data": "❌ Error extracting form data.",
            "supporting": "❌ Error generating supporting statement.",
            "debug": str(e)
        }














