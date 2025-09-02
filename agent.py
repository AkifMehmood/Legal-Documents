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
    """Return comprehensive UK legislation references using multiple search strategies."""
    try:
        refs = []
        # Multiple search strategies for UK legislation
        search_queries = [
            f"{query} UK legislation act statute",
            f"{query} legislation.gov.uk",
            f"{query} UK act parliament",
            f"site:legislation.gov.uk {query}",
            f"{query} UK law statute",
            f"{query} UK government legislation",
            f"legislation.gov.uk {query} UK"
        ]
        
        for search_query in search_queries:
            if len(refs) >= max_results * 2:
                break
                
            with DDGS() as ddgs:
                for r in ddgs.text(search_query, region="uk-en", safesearch="moderate", max_results=max_results * 2):
                    url = r.get("href") or r.get("url") or ""
                    if not url:
                        continue
                    # Prioritize UK legislation/government sources
                    host = url.lower()
                    if ("legislation.gov.uk" in host) or host.endswith(".gov.uk") or "://www.legislation.gov.uk" in host:
                        # Extract act/statute information
                        act_info = extract_legislation_info(url, r.get("title", ""))
                        refs.append({
                            "title": (r.get("title") or r.get("heading") or "UK Legislation"),
                            "url": url,
                            "snippet": r.get("body") or r.get("snippet") or "",
                            "act_info": act_info,
                            "source": "legislation.gov.uk"
                        })
                    if len(refs) >= max_results * 2:
                        break
        
        # Remove duplicates and sort by relevance
        seen_urls = set()
        unique_refs = []
        for ref in refs:
            if ref.get('url') not in seen_urls:
                seen_urls.add(ref.get('url'))
                unique_refs.append(ref)
        
        return unique_refs[:max_results]
    except Exception as e:
        print(f"⚠️ Legislation search failed: {e}")
        return []

def extract_legislation_info(url: str, title: str) -> str:
    """Extract legislation information from URL and title."""
    try:
        import re
        # Extract year from URL or title
        year_match = re.search(r'/(\d{4})/', url)
        year = year_match.group(1) if year_match else ""
        
        # Extract act type
        act_type = ""
        if "act" in title.lower():
            act_type = "Act"
        elif "statute" in title.lower():
            act_type = "Statute"
        elif "regulation" in title.lower():
            act_type = "Regulation"
        elif "order" in title.lower():
            act_type = "Order"
        
        # Build info string
        info_parts = []
        if act_type:
            info_parts.append(act_type)
        if year:
            info_parts.append(f"({year})")
        
        return " - ".join(info_parts) if info_parts else ""
    except Exception:
        return ""

def search_bailii_references(query: str, max_results: int = 5):
    """Return top BAILII case law references (title, url, snippet) with enhanced UK focus."""
    try:
        refs = []
        # Multiple search strategies to find real cases
        search_queries = [
            f"{query} UK case law judgment decision",
            f"{query} bailii.org UK",
            f"{query} court appeal high court UK",
            f"site:bailii.org {query} UK",
            f"{query} UK Supreme Court Court of Appeal",
            f"{query} UK legal case judgment",
            f"bailii.org {query} UK case law"
        ]
        
        for search_query in search_queries:
            if len(refs) >= max_results * 2:
                break
                
            with DDGS() as ddgs:
                for r in ddgs.text(search_query, region="uk-en", safesearch="moderate", max_results=max_results * 2):
                    url = r.get("href") or r.get("url") or ""
                    if not url:
                        continue
                    host = (url or "").lower()
                    if "bailii.org" in host:
                        # Prioritize UK cases and judgments
                        if any(keyword in host for keyword in ["/uk/cases/", "/uk/cmu/", "/uk/other/"]):
                            # Verify this is a real case by checking URL structure
                            if verify_bailii_case_url(url):
                                refs.append({
                                    "title": (r.get("title") or r.get("heading") or "BAILII UK Case"),
                                    "url": url,
                                    "snippet": r.get("body") or r.get("snippet") or "",
                                    "priority": "high"  # Mark UK cases as high priority
                                })
                        else:
                            refs.append({
                                "title": (r.get("title") or r.get("heading") or "BAILII case"),
                                "url": url,
                                "snippet": r.get("body") or r.get("snippet") or "",
                                "priority": "medium"
                            })
                    if len(refs) >= max_results * 3:  # Get more results to filter
                        break
        
        # Remove duplicates and sort by priority
        seen_urls = set()
        unique_refs = []
        for ref in refs:
            if ref.get('url') not in seen_urls:
                seen_urls.add(ref.get('url'))
                unique_refs.append(ref)
        
        # Sort by priority (UK cases first) and limit results
        unique_refs.sort(key=lambda x: 0 if x.get("priority") == "high" else 1)
        return unique_refs[:max_results]
    except Exception as e:
        print(f"⚠️ BAILII search failed: {e}")
        return []

def verify_bailii_case_url(url: str) -> bool:
    """Verify that a BAILII URL is a real case URL."""
    try:
        # Check if URL has proper BAILII case structure
        if not url or "bailii.org" not in url:
            return False
        
        # Check for proper case URL patterns
        case_patterns = [
            "/uk/cases/EWCA/",  # Court of Appeal
            "/uk/cases/EWHC/",  # High Court
            "/uk/cases/UKSC/",  # Supreme Court
            "/uk/cases/UKPC/",  # Privy Council
            "/uk/cases/UKHL/",  # House of Lords
            "/uk/cases/UKET/",  # Employment Tribunal
            "/uk/cases/UKUT/",  # Upper Tribunal
            "/uk/cases/UKFTT/", # First-tier Tribunal
        ]
        
        return any(pattern in url for pattern in case_patterns)
    except Exception:
        return False



def bailii_lucy_search(query: str, max_results: int = 5):
    """Query BAILII Lucy search and parse case results with titles and links, focusing on UK solved cases."""
    try:
        # Multiple search strategies for real cases
        search_queries = [
            f"{query} UK judgment decision",
            f"{query} immigration employment",
            f"{query} illegal workers",
            f"{query} right to work",
            f"{query} employer penalty",
            f"{query} UK case law",
            f"{query} court judgment",
            f"{query} legal decision",
            f"immigration employment UK",
            f"illegal workers UK",
            f"employment law UK"
        ]
        
        all_results = []
        
        for enhanced_query in search_queries:
            if len(all_results) >= max_results * 2:
                break
                
            params = {"q": enhanced_query}
            url = f"https://www.bailii.org/cgi-bin/lucy_search_1.cgi?{urlencode(params)}"
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                continue
                
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
                    
                # Only include verified real case URLs
                if verify_bailii_case_url(full):
                    # Extract case year and court info if available
                    case_info = extract_case_info_from_url(full, text)
                    results.append({
                        "title": text, 
                        "url": full,
                        "case_info": case_info,
                        "priority": "high"
                    })
                elif "bailii.org" in full and "/ie/cases/" in full:
                    # Include Irish cases as medium priority
                    case_info = extract_case_info_from_url(full, text)
                    results.append({
                        "title": text, 
                        "url": full,
                        "case_info": case_info,
                        "priority": "medium"
                    })
                    
                if len(results) >= max_results:
                    break
            
            all_results.extend(results)
        
        # Remove duplicates
        seen_urls = set()
        unique_results = []
        for result in all_results:
            if result.get('url') not in seen_urls:
                seen_urls.add(result.get('url'))
                unique_results.append(result)
        
        # Sort by priority and limit results
        unique_results.sort(key=lambda x: 0 if x.get("priority") == "high" else 1)
        return unique_results[:max_results]
    except Exception as e:
        print(f"⚠️ BAILII Lucy parse failed: {e}")
        return []

def extract_case_info_from_url(url: str, title: str) -> str:
    """Extract case information from BAILII URL and title."""
    try:
        # Extract year from URL or title
        import re
        year_match = re.search(r'/(\d{4})/', url)
        year = year_match.group(1) if year_match else ""
        
        # Extract court from URL path
        court = ""
        if "/uk/cases/EWCA/" in url:
            court = "Court of Appeal"
        elif "/uk/cases/EWHC/" in url:
            court = "High Court"
        elif "/uk/cases/UKSC/" in url:
            court = "Supreme Court"
        elif "/uk/cases/UKPC/" in url:
            court = "Privy Council"
        elif "/uk/cases/UKHL/" in url:
            court = "House of Lords"
        
        # Build case info string
        info_parts = []
        if court:
            info_parts.append(court)
        if year:
            info_parts.append(f"({year})")
        
        return " - ".join(info_parts) if info_parts else ""
    except Exception:
        return ""

def fetch_bailii_judgment_summary(url: str) -> str:
    """Fetch a BAILII judgment page and attempt to extract a brief outcome summary with enhanced UK case focus."""
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        text_blocks = [p.get_text(" ", strip=True) for p in soup.select("p, div")]
        candidates = []
        
        # Enhanced keywords for UK case outcomes
        outcome_keywords = [
            "held", "conclusion", "decision", "disposition", "order", 
            "judgment", "ruling", "finding", "determination", "verdict",
            "appeal allowed", "appeal dismissed", "claim succeeded", "claim failed",
            "liability", "damages", "injunction", "declaration"
        ]
        
        for t in text_blocks:
            tl = t.lower()
            if any(k in tl for k in outcome_keywords):
                # Prioritize longer, more detailed outcomes
                if len(t) > 50:  # Skip very short snippets
                    candidates.append((t, len(t)))
        
        if candidates:
            # Sort by length (longer = more detailed) and take the best
            candidates.sort(key=lambda x: x[1], reverse=True)
            best = candidates[0][0]
            if len(best) > 500:
                best = best[:500].rsplit(" ", 1)[0] + "…"
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

    # ==================== USE ALL THREE APIs ====================
    
    # Only use Legislation API for references
    legislation_query = build_legislation_query(question)
    legislation_refs = search_legislation_references(legislation_query, max_results=3)
    
    # Disable BAILII API - only use Legislation API
    bailii_refs = []
    
    # Only use Legislation API - no BAILII or Gemini processing needed
    refs_legislation_block = chr(10).join([
        f"• {ref['title']}" + 
        (f" {ref.get('act_info', '')}" if ref.get('act_info') else "") + 
        f"{chr(10)}  Link: {ref['url']}" + 
        (f"{chr(10)}  Note: {ref['snippet']}" if ref.get('snippet') else "")
        for ref in legislation_refs
    ]) if legislation_refs else "- No UK legal sources found for this query."
    
    refs_bailii_block = ""  # No BAILII references
    
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
5. **UK LAW**: Consider UK legal framework. Where relevant, compare the document to the statutes below and cite them inline.
6. **MANDATORY UK CASE REFERENCES**: YOU MUST ALWAYS provide 2-3 relevant UK case references from the sources below. These are already provided for you - use them in your response. Focus on cases that demonstrate similar legal principles or outcomes.
7. **SIMILAR CASES**: If a case name is detected (e.g., "{primary_case or 'N/A'}"), list similar UK cases with brief outcomes.
8. **DOCUMENT REVIEW**: If asked about document review, analyze the uploaded content for:
   - Missing important clauses
   - Potential legal risks
   - Areas for improvement
   - Compliance with UK law
9. **SPECIFIC REFERENCES**: Quote or reference specific parts of the uploaded document
10. **PROFESSIONAL ADVICE**: Provide practical, actionable legal insights
11. **DOCUMENT CONTENT FOCUS**: Always prioritize the uploaded document content in your response
12. **QUOTE SPECIFIC SECTIONS**: When possible, quote or reference specific parts of the document
13. **UK CASE FOCUS**: Prioritize UK cases that have been resolved and provide clear outcomes
14. **CRITICAL**: You MUST include the UK case references provided below in your response. Do not say you cannot provide references - they are provided for you to use.
15. **SIMILAR CASES REQUEST**: If the user asks for "similar cases", "related cases", or "different cases", you MUST provide the case references below as examples of similar solved cases.
16. **IMPORTANT**: Do NOT mention "legislation", "BAILII", or any API names in your response. Present the legal references naturally as UK law and case law.

UK LEGAL SOURCES:
{refs_legislation_block}

SIMILAR UK CASES DETECTED:
{chr(10).join([f"🔍 {c['title']}" + (f" {c.get('case_info', '')}" if c.get('case_info') else "") + f": {c['url']}" + (f"{chr(10)}   📋 Outcome: {c['snippet']}" if c.get('snippet') else '') for c in similar_cases]) if similar_cases else "- No similar cases auto-detected"}

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
2. For legal advice questions, consider UK law. Compare relevant points to the statutes below when appropriate, and cite them. Also consider UK case law and cite relevant cases.
3. **MANDATORY**: YOU MUST ALWAYS provide 2-3 UK case references from the sources provided below. These are already found for you - use them in your response.
4. Be professional, clear, and concise
5. If you don't have enough information, suggest uploading relevant documents
6. Focus on case management and general legal guidance
7. **UK CASE FOCUS**: Prioritize UK cases that have been resolved and provide clear outcomes
8. **CRITICAL**: You MUST include the UK case references provided below in your response. Do not say you cannot provide references - they are provided for you to use.
9. **SIMILAR CASES REQUEST**: If the user asks for "similar cases", "related cases", or "different cases", you MUST provide the case references below as examples of similar solved cases.
10. **IMPORTANT**: Do NOT mention "legislation", "BAILII", or any API names in your response. Present the legal references naturally as UK law and case law.

UK LEGAL SOURCES:
{refs_legislation_block}

Please provide a helpful response based on the available information.
"""
    try:
        response = model.generate_content(prompt)
        answer = response.text.strip()
        # Append simple bullet point references
        if legislation_refs:
            refs_list = []
            refs_list.append("REFERENCES:")
            for r in legislation_refs:
                leg_ref = f"• {r['title']}"
                if r.get('act_info'):
                    leg_ref += f" - {r['act_info']}"
                leg_ref += f"{chr(10)}  Link: {r['url']}"
                if r.get('snippet'):
                    leg_ref += f"{chr(10)}  Note: {r['snippet']}"
                refs_list.append(leg_ref)
            answer = f"{answer}{chr(10)}{chr(10)}{chr(10).join(refs_list)}"
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

def search_legal_references_with_gemini(question: str, document: str = "") -> list:
    """Use Gemini API to find relevant UK legal references and cases."""
    try:
        prompt = f"""
You are a UK legal research assistant. Based on the following question and document context, provide 3-5 specific UK legal references including:

1. Relevant UK legislation (Acts, Statutes, Regulations)
2. Relevant UK case law (Supreme Court, Court of Appeal, High Court cases)
3. Specific legal principles and precedents

Question: {question}

Document Context: {document[:1000] if document else "No document provided"}

Please provide specific UK legal references in this format:
LEGISLATION:
- [Act Name] ([Year]) - [Brief description]
- [Regulation Name] ([Year]) - [Brief description]

CASE LAW:
- [Case Name] - [Court] ([Year]) - [Brief outcome]
- [Case Name] - [Court] ([Year]) - [Brief outcome]

Focus on UK law only. Be specific with case names, court names, and years.
"""
        
        response = call_gemini_api(prompt)
        
        # Parse the response to extract references
        references = []
        lines = response.split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('LEGISLATION:'):
                current_section = 'legislation'
            elif line.startswith('CASE LAW:'):
                current_section = 'case_law'
            elif line.startswith('- ') and current_section:
                ref_text = line[2:].strip()
                if current_section == 'legislation':
                    references.append({
                        'type': 'legislation',
                        'title': ref_text,
                        'source': 'gemini_api'
                    })
                elif current_section == 'case_law':
                    references.append({
                        'type': 'case_law',
                        'title': ref_text,
                        'source': 'gemini_api'
                    })
        
        return references
    except Exception as e:
        print(f"⚠️ Gemini legal search failed: {e}")
        return []

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














