"""
UNIVERSAL PRODUCT CATALOG SCRAPER
==================================

A zero-hardcoding web scraper that extracts product information from any e-commerce site.

DESIGN PRINCIPLES:
1. NO hardcoded selectors
2. NO site-specific logic
3. Adapts to any HTML structure through pattern recognition
4. Handles multiple pagination types automatically

AUTHOR: Senior Scraping Engineer
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field, ConfigDict
import json
import time
import re
import hashlib


# ============================================================================
# DATA MODELS
# ============================================================================

class Product(BaseModel):
    """Standardized product data structure - same across all sites"""
    # Pydantic V2 syntax for config
    model_config = ConfigDict(frozen=False) 
    
    name: str
    price: Optional[str] = None
    image_url: Optional[str] = None
    availability: str = "Unknown"
    product_url: Optional[str] = None


class ScrapingResult(BaseModel):
    """Complete scraping session result"""
    url: str
    total_products: int
    products: List[Product]
    pagination_method: Optional[str] = None
    scraping_time_seconds: float
    errors: List[str] = Field(default_factory=list)


# ============================================================================
# CORE SCRAPER CLASS
# ============================================================================

class UniversalProductScraper:
    """
    Main scraper class that works across ANY e-commerce site
    without hardcoded selectors or site-specific logic
    """
    
    def __init__(self, headless: bool = True, timeout: int = 60000):
        self.headless = headless
        self.timeout = timeout
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        """Context manager entry"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            locale='en-US'
        )
        self.page = self.context.new_page()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    # ========================================================================
    # PHASE 1: INTELLIGENT PAGE LOADING
    # ========================================================================
    
    def load_page_intelligently(self, url: str) -> bool:
        """
        Load page and wait for JS to fully render.
        Returns True if successful, False otherwise.
        
        STRATEGY:
        - Multiple wait conditions (not just one)
        - Validates content actually rendered
        - Handles SPA hydration delays
        """
        try:
            print(f"📄 Loading: {url}")
            
            # Step 1: Navigate
            self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            
            # Step 2: Wait for network to settle
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                print("  ⚠️  Network didn't stabilize, continuing...")
            
            # Step 3: Extra wait for JS frameworks (React/Vue hydration)
            time.sleep(3)
            
            # Step 4: Validate content loaded
            validation = self.page.evaluate("""
                () => {
                    const imgCount = document.querySelectorAll('img').length;
                    const linkCount = document.querySelectorAll('a').length;
                    const textLength = document.body.innerText.length;
                    
                    return {
                        has_images: imgCount > 0,
                        has_links: linkCount > 5,
                        has_content: textLength > 100,
                        image_count: imgCount,
                        link_count: linkCount
                    };
                }
            """)
            
            if not validation['has_content']:
                print(f"  ❌ Page appears empty (text length: {validation.get('text_length', 0)})")
                return False
            
            print(f"  ✅ Page loaded ({validation['image_count']} images, {validation['link_count']} links)")
            return True
            
        except Exception as e:
            print(f"  ❌ Error loading page: {e}")
            return False
    
    # ========================================================================
    # PHASE 2: PRODUCT CONTAINER DETECTION (NO HARDCODING)
    # ========================================================================
    
    def detect_product_containers(self) -> List[str]:
        """
        Find product containers using STRUCTURAL PATTERN RECOGNITION.
        
        LOGIC:
        1. Find all visible elements with images (products need images)
        2. Group elements by structural fingerprint:
           - Tag name
           - Number of children
           - Has image, has link, has text
        3. The largest repeating group = products
        4. Return XPath selectors to those elements
        
        NO CSS CLASSES. NO HARDCODED SELECTORS.
        """
        
        print("\n🔍 Detecting product containers...")
        
        container_xpaths = self.page.evaluate("""
            () => {
                // Helper: Get XPath for an element
                function getXPath(element) {
                    if (!element || element.nodeType !== 1) return null;
                    if (element.id) return `//*[@id="${element.id}"]`;
                    if (element === document.body) return '/html/body';
                    
                    let ix = 0;
                    const siblings = element.parentNode?.children || [];
                    
                    for (let i = 0; i < siblings.length; i++) {
                        const sibling = siblings[i];
                        if (sibling === element) {
                            const tagName = element.tagName.toLowerCase();
                            const parent = element.parentNode;
                            const parentPath = parent ? getXPath(parent) : '';
                            return `${parentPath}/${tagName}[${ix + 1}]`;
                        }
                        if (sibling.tagName === element.tagName) {
                            ix++;
                        }
                    }
                    return null;
                }
                
                // Step 1: Get all visible elements
                const allElements = Array.from(document.querySelectorAll('*'));
                const visibleElements = allElements.filter(el => {
                    const rect = el.getBoundingClientRect();
                    // Must be visible and reasonable size
                    return rect.width > 100 && rect.height > 100 && rect.top < 10000;
                });
                
                // Step 2: Filter for elements that look like product containers
                const candidates = visibleElements.filter(el => {
                    const hasImage = el.querySelector('img') !== null;
                    const hasText = (el.innerText || '').trim().length > 5;
                    // Product must have image and text
                    return hasImage && hasText;
                });
                
                // Step 3: Create structural fingerprints
                const fingerprints = new Map();
                
                candidates.forEach(el => {
                    const tagName = el.tagName;
                    
                    // Get DOM depth (distance from body)
                    let depth = 0;
                    let current = el;
                    while (current.parentNode && current !== document.body) {
                        depth++;
                        current = current.parentNode;
                    }
                    
                    // Fingerprint purely by Tag Name and Depth
                    // This is vastly superior to childCount/textLength as it ignores variations like "Sale" badges
                    const fingerprint = `${tagName}|depth:${depth}`;
                    
                    if (!fingerprints.has(fingerprint)) {
                        fingerprints.set(fingerprint, []);
                    }
                    
                    fingerprints.get(fingerprint).push(el);
                });
                
                // Step 4: Find the group with most repetitions
                let largestGroup = [];
                let bestFingerprint = null;
                
                for (const [fp, elements] of fingerprints.entries()) {
                    // Need at least 3 similar elements to consider it a pattern
                    if (elements.length >= 3 && elements.length > largestGroup.length) {
                        largestGroup = elements;
                        bestFingerprint = fp;
                    }
                }
                
                // Step 5: Return XPaths of these elements
                const xpaths = largestGroup
                    .map(el => getXPath(el))
                    .filter(xpath => xpath !== null);
                
                return xpaths;
            }
        """)
        
        print(f"  ✅ Found {len(container_xpaths)} product containers")
        return container_xpaths
    
    # ========================================================================
    # PHASE 3: DATA EXTRACTION (SEMANTIC, NOT SELECTOR-BASED)
    # ========================================================================
    
    def extract_product_from_container(self, xpath: str) -> Optional[Product]:
        """
        Extract product data from a container using SEMANTIC PATTERNS.
        
        LOGIC:
        - Name: Find headings or longest text node
        - Price: Regex match for currency patterns
        - Image: Find img tag, check src and data-src
        - URL: Find link tag
        - Availability: Text pattern matching
        
        NO HARDCODED SELECTORS. Works on any site structure.
        """
        
        try:
            product_data = self.page.evaluate(f"""
                (xpath) => {{
                    // Get the container element
                    const container = document.evaluate(
                        xpath,
                        document,
                        null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE,
                        null
                    ).singleNodeValue;
                    
                    if (!container) return null;
                    
                    // === EXTRACT NAME ===
                    const getName = () => {{
                        // Priority 1: Heading tags (most likely product title)
                        const heading = container.querySelector('h1, h2, h3, h4, h5, h6');
                        if (heading && heading.innerText.trim().length > 3) {{
                            return heading.innerText.trim();
                        }}
                        
                        // Priority 2: Link text (products usually link to detail pages)
                        const links = Array.from(container.querySelectorAll('a'));
                        for (const link of links) {{
                            const text = link.innerText.trim();
                            if (text.length >= 10 && text.length < 200) {{
                                return text;
                            }}
                        }}
                        
                        // Priority 3: Longest text node in container
                        const allText = container.innerText || '';
                        const lines = allText.split('\\n')
                            .map(l => l.trim())
                            .filter(l => l.length >= 10 && l.length < 200)
                            .filter(l => !/^\\d+$/.test(l));  // Not just numbers
                        
                        if (lines.length > 0) {{
                            return lines[0];
                        }}
                        
                        return null;
                    }};
                    
                    // === EXTRACT PRICE ===
                    const getPrice = () => {{
                        const allText = container.innerText || '';
                        
                        // Comprehensive price patterns
                        const patterns = [
                            /\\$\\s*([\\d,]+(?:\\.\\d{{2}})?)/i,           // $99.99
                            /£\\s*([\\d,]+(?:\\.\\d{{2}})?)/i,            // £99.99
                            /€\\s*([\\d,]+(?:\\.\\d{{2}})?)/i,            // €99.99
                            /Rs\\.?\\s*([\\d,]+(?:\\.\\d{{2}})?)/i,       // Rs 999 or Rs. 999
                            /₹\\s*([\\d,]+(?:\\.\\d{{2}})?)/i,            // ₹999
                            /([\\d,]+\\.\\d{{2}})\\s*(?:USD|GBP|EUR|INR)/i, // 99.99 USD
                            /\\b([\\d,]+)\\s*(?:USD|GBP|EUR|Rs|PKR)\\b/i  // 99 USD
                        ];
                        
                        for (const pattern of patterns) {{
                            const match = allText.match(pattern);
                            if (match) {{
                                return match[0].trim();
                            }}
                        }}
                        
                        return null;
                    }};
                    
                    // === EXTRACT IMAGE ===
                    const getImage = () => {{
                        const img = container.querySelector('img');
                        if (!img) return null;
                        
                        // Check multiple attributes (lazy loading support)
                        return img.src || 
                               img.dataset.src || 
                               img.dataset.lazySrc ||
                               img.getAttribute('data-src') ||
                               null;
                    }};
                    
                    // === EXTRACT PRODUCT URL ===
                    const getProductUrl = () => {{
                        const link = container.querySelector('a');
                        return link ? link.href : null;
                    }};
                    
                    // === EXTRACT AVAILABILITY ===
                    const getAvailability = () => {{
                        const text = (container.innerText || '').toLowerCase();
                        
                        // Out of stock indicators
                        const outOfStockPatterns = [
                            'out of stock',
                            'sold out',
                            'unavailable',
                            'not available',
                            'coming soon'
                        ];
                        
                        for (const pattern of outOfStockPatterns) {{
                            if (text.includes(pattern)) {{
                                return 'Out of Stock';
                            }}
                        }}
                        
                        // In stock indicators
                        const inStockPatterns = [
                            'in stock',
                            'available',
                            'add to cart',
                            'buy now'
                        ];
                        
                        for (const pattern of inStockPatterns) {{
                            if (text.includes(pattern)) {{
                                return 'In Stock';
                            }}
                        }}
                        
                        // Check for disabled buttons
                        const button = container.querySelector('button[disabled], .disabled');
                        if (button) return 'Out of Stock';
                        
                        // Default: assume available if product is shown
                        return 'In Stock';
                    }};
                    
                    // === RETURN EXTRACTED DATA ===
                    return {{
                        name: getName(),
                        price: getPrice(),
                        image_url: getImage(),
                        product_url: getProductUrl(),
                        availability: getAvailability()
                    }};
                }}
            """, xpath)
            
            # Validate we got at least a name
            if product_data and product_data.get('name'):
                return Product(**product_data)
            
            print(f"    [Debug] Extraction skipped (No valid product name found). Raw data: {product_data}")
            return None
            
        except Exception as e:
            # Silently skip failed extractions
            print(f"    [Debug] Extraction failed with exception: {str(e)}")
            return None
    
    # ========================================================================
    # PHASE 4: PAGINATION DETECTION (BEHAVIORAL, NOT SELECTOR-BASED)
    # ========================================================================
    
    def detect_pagination_method(self) -> Optional[str]:
        """
        Detect HOW this site loads more products.
        
        TESTS (in order):
        1. Scroll test - does scrolling load more products?
        2. Button test - is there a "Load More" / "Next" button?
        3. URL test - does URL have pagination parameters?
        
        Returns: 'scroll', 'button', 'url', or None
        """
        
        print("\n🔍 Detecting pagination method...")
        
        # Get initial state
        initial_count = len(self.detect_product_containers())
        initial_height = self.page.evaluate("() => document.body.scrollHeight")
        
        # === TEST 1: SCROLL-BASED ===
        print("  Testing scroll behavior...")
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)  # Wait for lazy load
        
        new_count = len(self.detect_product_containers())
        new_height = self.page.evaluate("() => document.body.scrollHeight")
        
        if new_count > initial_count or new_height > initial_height:
            print(f"  ✅ INFINITE SCROLL detected (+{new_count - initial_count} products)")
            # Scroll back to top
            self.page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)
            return 'scroll'
        
        # === TEST 2: BUTTON-BASED ===
        print("  Testing for pagination buttons...")
        button_exists = self.page.evaluate("""
            () => {
                const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                const keywords = ['next', 'more', 'load more', 'show more', 'view more', '→', '>'];
                
                for (const btn of buttons) {
                    const text = (btn.innerText || '').toLowerCase();
                    const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
                    const combinedText = text + ' ' + ariaLabel;
                    
                    if (keywords.some(kw => combinedText.includes(kw))) {
                        return true;
                    }
                }
                return false;
            }
        """)
        
        if button_exists:
            print("  ✅ BUTTON pagination detected")
            return 'button'
        
        # === TEST 3: URL-BASED ===
        print("  Testing for URL pagination...")
        current_url = self.page.url
        url_params = re.findall(r'[?&](page|p|start|offset|from)=(\d+)', current_url, re.IGNORECASE)
        
        if url_params:
            print(f"  ✅ URL pagination detected: {url_params[0]}")
            return 'url'
        
        print("  ❌ No pagination detected")
        return None
    
    # ========================================================================
    # PHASE 5: PAGINATION EXECUTION
    # ========================================================================
    
    def execute_dynamic_pagination(self, max_pages: int = 30) -> List[str]:
        """
        Dynamically handles BOTH scrolling and 'Load More' buttons in a single hybrid loop.
        Many modern sites scroll for a few pages, then show a button, then scroll again.
        """
        print(f"\n🔄 Executing dynamic pagination (max {max_pages} pages)...")
        visited_urls = [self.page.url]
        
        for i in range(max_pages):
            before_count = len(self.detect_product_containers())
            before_url = self.page.url
            
            # 1. Try to find and click a 'Load More' button first
            button_clicked = self.page.evaluate("""
                () => {
                    const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"], span[role="button"]'));
                    const keywords = ['next', 'more', 'load more', 'show more', 'view more', 'discover'];
                    
                    for (const btn of buttons) {
                        const text = (btn.innerText || '').toLowerCase();
                        if (keywords.some(kw => text.includes(kw)) && btn.offsetParent !== null) {
                            btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                            // Dispatch standard click event to bypass strict framework listeners
                            btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return true;
                        }
                    }
                    return false;
                }
            """)
            
            if button_clicked:
                print("  🔘 Clicked 'Load More' button...")
                time.sleep(2)

            # 1b. Try numbered/next-link pagination when available
            link_target = self.page.evaluate("""
                () => {
                    const candidates = Array.from(document.querySelectorAll('a[rel="next"], a, button'));
                    const current = new URL(window.location.href);
                    const blockedPathKeywords = ['account', 'login', 'signin', 'register', 'cart', 'checkout', 'customer'];

                    const isVisible = (el) => {
                        const rect = el.getBoundingClientRect();
                        return el.offsetParent !== null && rect.width > 0 && rect.height > 0;
                    };

                    const isSafeHref = (href) => {
                        if (!href) return false;
                        try {
                            const u = new URL(href, window.location.href);
                            if (u.origin !== current.origin) return false;
                            const path = u.pathname.toLowerCase();
                            if (blockedPathKeywords.some(k => path.includes(k))) return false;

                            // Keep same collection/catalog path as strong signal.
                            const samePath = (u.pathname === current.pathname);
                            const pageParam = u.searchParams.has('page') || u.searchParams.has('p') || u.searchParams.has('offset');
                            return samePath || pageParam;
                        } catch {
                            return false;
                        }
                    };

                    const getText = (el) => ((el.innerText || '').trim().toLowerCase() + ' ' + (el.getAttribute('aria-label') || '').toLowerCase());

                    // 1) standards-based rel=next
                    for (const el of candidates) {
                        if (!isVisible(el)) continue;
                        if (el.matches('a[rel="next"]') && isSafeHref(el.href)) return { mode: 'goto', value: el.href };
                    }

                    // 2) semantic next controls
                    const keywords = ['next', 'load more', 'show more', 'view more', 'older', '→', '›'];
                    for (const el of candidates) {
                        if (!isVisible(el)) continue;
                        const txt = getText(el);
                        if (keywords.some(k => txt.includes(k))) {
                            if (el.tagName.toLowerCase() === 'a' && isSafeHref(el.href)) return { mode: 'goto', value: el.href };
                            return { mode: 'click', value: null };
                        }
                    }

                    // 3) numbered pagination: click next numeric after current page
                    const numeric = candidates
                        .filter(el => isVisible(el))
                        .map(el => ({
                            el,
                            text: (el.innerText || '').trim(),
                            current: el.getAttribute('aria-current') === 'page' || el.classList.contains('active') || el.classList.contains('current')
                        }))
                        .filter(x => /^\d+$/.test(x.text));

                    const current = numeric.find(x => x.current);
                    if (current) {
                        const nextNum = String(parseInt(current.text, 10) + 1);
                        const target = numeric.find(x => x.text === nextNum);
                        if (target) {
                            if (target.el.tagName.toLowerCase() === 'a' && isSafeHref(target.el.href)) return { mode: 'goto', value: target.el.href };
                            return { mode: 'click', value: null };
                        }
                    }

                    return null;
                }
            """)

            if link_target:
                print("  📄 Triggered link-based pagination...")
                if link_target.get('mode') == 'goto' and link_target.get('value'):
                    try:
                        self.page.goto(link_target['value'], wait_until="domcontentloaded", timeout=self.timeout)
                        self.page.wait_for_load_state("networkidle", timeout=12000)
                    except PlaywrightTimeout:
                        time.sleep(2)
                else:
                    self.page.evaluate("""
                        () => {
                            const candidates = Array.from(document.querySelectorAll('a, button'));
                            const blockedWords = ['account', 'login', 'sign in', 'register', 'cart', 'checkout'];
                            const isVisible = (el) => {
                                const rect = el.getBoundingClientRect();
                                return el.offsetParent !== null && rect.width > 0 && rect.height > 0;
                            };
                            const keywords = ['next', 'load more', 'show more', 'view more', 'older', '→', '›'];
                            for (const el of candidates) {
                                if (!isVisible(el)) continue;
                                const txt = ((el.innerText || '').trim().toLowerCase() + ' ' + (el.getAttribute('aria-label') || '').toLowerCase());
                                if (blockedWords.some(k => txt.includes(k))) continue;
                                if (keywords.some(k => txt.includes(k))) {
                                    el.click();
                                    return;
                                }
                            }
                        }
                    """)
                    time.sleep(2)
            
            # 2. Perform a smooth scroll to trigger lazy loaders and Intersection Observers
            self.page.evaluate("""
                async () => {
                    await new Promise((resolve) => {
                        let totalHeight = 0;
                        const distance = 600;
                        let scrolls = 0;
                        const maxScrolls = 15; // Prevent infinite scroll lock
                        
                        const timer = setInterval(() => {
                            const scrollHeight = document.body.scrollHeight;
                            window.scrollBy(0, distance);
                            totalHeight += distance;
                            scrolls++;

                            if (totalHeight >= scrollHeight || scrolls >= maxScrolls) {
                                clearInterval(timer);
                                resolve();
                            }
                        }, 150);
                    });
                }
            """)
            
            # 3. Poll actively for container count to increase (handles slow network)
            waited = 0
            new_count = before_count
            while waited < 8:  # Wait up to 8 seconds for network
                time.sleep(2)
                waited += 2
                new_count = len(self.detect_product_containers())
                if new_count > before_count:
                    break
            
            # 4. Wiggle fallback if stuck
            if new_count == before_count:
                self.page.evaluate("window.scrollBy(0, -800);")
                time.sleep(1)
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                new_count = len(self.detect_product_containers())
            
            # 5. Check iteration results
            after_url = self.page.url
            if after_url not in visited_urls:
                visited_urls.append(after_url)

            if new_count > before_count:
                print(f"  Page {i + 1}: {before_count} → {new_count} products")
            elif after_url != before_url:
                print(f"  Page {i + 1}: navigated to next page ({after_url})")
            else:
                print(f"  ✅ Reached end of catalog after {i + 1} pages")
                break

        return visited_urls
    
    # ========================================================================
    # MAIN SCRAPING ORCHESTRATOR
    # ========================================================================
    
    def scrape(self, url: str) -> ScrapingResult:
        """
        Main scraping function that orchestrates the entire process.
        """
        
        start_time = time.time()
        errors = []
        
        print(f"\n{'='*80}")
        print(f"🚀 STARTING SCRAPE: {url}")
        print(f"{'='*80}\n")
        
        # Step 1: Load page
        if not self.load_page_intelligently(url):
            errors.append("Failed to load page")
            return ScrapingResult(
                url=url,
                total_products=0,
                products=[],
                scraping_time_seconds=time.time() - start_time,
                errors=errors
            )
        
        # Step 2: Detect product containers
        container_xpaths = self.detect_product_containers()
        
        if not container_xpaths:
            errors.append("No product containers detected")
            return ScrapingResult(
                url=url,
                total_products=0,
                products=[],
                scraping_time_seconds=time.time() - start_time,
                errors=errors
            )
        
        # Step 3: Extract initial batch of products
        print(f"\n📦 Extracting products from {len(container_xpaths)} containers...")
        all_products = []
        
        for i, xpath in enumerate(container_xpaths, 1):
            product = self.extract_product_from_container(xpath)
            if product:
                all_products.append(product)
            
            if i % 10 == 0:
                print(f"  Processed {i}/{len(container_xpaths)} containers...")
        
        print(f"  ✅ Extracted {len(all_products)} products from first page")
        
        # Step 4: Detect pagination method (Mostly for logging now, as we use dynamic execution)
        pagination_method = self.detect_pagination_method()
        
        # Step 5: Execute universal dynamic pagination
        visited_urls = self.execute_dynamic_pagination()

        # Step 6: Re-extract all products after full pagination
        print(f"\n📦 Finalizing extraction of all loaded products...")
        all_products_with_id: List[Tuple[Product, str]] = []
        failed_extractions = 0

        # For numbered pagination, collect from each visited page URL.
        for page_idx, page_url in enumerate(visited_urls, 1):
            if self.page.url != page_url:
                self.load_page_intelligently(page_url)

            container_xpaths = self.detect_product_containers()
            print(f"  Page snapshot {page_idx}/{len(visited_urls)}: {len(container_xpaths)} containers")

            for i, xpath in enumerate(container_xpaths, 1):
                product = self.extract_product_from_container(xpath)
                if product:
                    all_products_with_id.append((product, f"{page_idx}:{i}:{xpath}"))
                else:
                    failed_extractions += 1
                    
                if i % 20 == 0:
                    print(f"  Processed {i}/{len(container_xpaths)} containers...")
                
        print(f"  ✅ Successfully extracted {len(all_products_with_id)} products ({failed_extractions} containers skipped)")
        
        # Step 7: Deduplicate products (by name + price + url + image)
        print(f"\n🧹 Deduplicating products...")
        unique_products = {}
        duplicates_dropped = 0
        
        for product, _ in all_products_with_id:
            # Keep original straightforward dedupe identity to avoid false merges.
            key = f"{product.name}_{product.price}_{product.product_url}_{product.image_url}"
            if key not in unique_products:
                unique_products[key] = product
            else:
                duplicates_dropped += 1
                print(f"    [Debug] Dropping duplicate: {product.name} | {product.price} | {product.product_url}")
        
        final_products = list(unique_products.values())
        print(f"  ✅ Kept {len(final_products)} unique products ({duplicates_dropped} duplicates dropped)")
        
        # Step 8: Return results
        elapsed_time = time.time() - start_time
        
        print(f"\n{'='*80}")
        print(f"✅ SCRAPING COMPLETE")
        print(f"{'='*80}")
        print(f"Total unique products: {len(final_products)}")
        print(f"Pagination method: {pagination_method or 'None'}")
        print(f"Time elapsed: {elapsed_time:.2f} seconds")
        print(f"{'='*80}\n")
        
        return ScrapingResult(
            url=url,
            total_products=len(final_products),
            products=final_products,
            pagination_method=pagination_method,
            scraping_time_seconds=elapsed_time,
            errors=errors
        )


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

def main():
    """
    Example usage of the Universal Product Scraper
    """
    
    # URLs to scrape
    test_urls = [
        "https://www.happysocks.com/uk/kids",
        "https://labbada.com/"
    ]
    
    results = []
    
    for url in test_urls:
        with UniversalProductScraper(headless=True) as scraper:
            result = scraper.scrape(url)
            results.append(result)
            
            # Save results to JSON in the CURRENT folder, not an absolute path
            output_file = f"scrape_results_{int(time.time())}.json"
            
            with open(output_file, 'w', encoding='utf-8') as f:
                # Updated to Pydantic V2 syntax (model_dump instead of dict)
                json.dump(result.model_dump(), f, indent=2, default=str)
            
            print(f"Results saved to: {output_file}")
    
    return results


if __name__ == "__main__":
    main()
