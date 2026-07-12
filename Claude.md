\# CLAUDE.md (CS2 饰品跨平台搬砖项目规范)



Behavioral guidelines to reduce common LLM coding mistakes in this specific CS2 Arbitrage Project.



\---



\## 1. Think Before Coding (CS2 Arbitrage Context)

\*\*Don't assume data structures. Surface platform limitations.\*\*

Before implementing or modifying code:

\- \*\*Identifier Strategy:\*\* Always assume global asset alignment is based \*\*strictly\*\* on `market\_hash\_name` (e.g., `"AK-47 | Printstream (Field-Tested)"`). Never use Chinese names for multi-platform data merging.

\- \*\*State Assumptions:\*\* Explicitly state if you assume an API response format. If a platform's JSON structure changes, ask the user to provide a fresh raw snippet before rewriting parsers.



\## 2. Simplicity \& Minimalist Toolkit

\*\*Minimum code that fetches and calculates. No over-engineering.\*\*

\- \*\*Tech Stack Guardrails:\*\* Use ONLY `requests` and `pandas` (or native dictionaries). Do NOT introduce asynchronous programming (`asyncio`) or database ORMs.

\- \*\*Currency \& Constants:\*\* 

&#x20; - Skinport query parameters MUST use `currency=HKD` to fetch prices in Hong Kong Dollars.

&#x20; - Fetch the live HKD-to-RMB exchange rate once at program startup via `src/Exchange_Rate.py`, then reuse the returned `cny_per_hkd` for the whole analyzer run.

&#x20; - Small-ticket workflow ceilings: Skinport `min_price <= 2300 HKD`, UU `yyyp_sell_price <= 2000 RMB`. Higher-value items should be handled by a separate future module.

\- \*\*Pure Pricing Formula (HKD to RMB):\*\* Strictly adhere to the pure raw arbitrage formula without pre-deducting platform fees:

&#x20; $$\\text{Expected Exit Price} = \\text{UU Buy Price} + \\text{Dynamic Fill Coefficient} \\times (\\text{UU Sell Price} - \\text{UU Buy Price})$$

&#x20; $$\\text{Listing Profit Rate} = \\frac{\\text{Expected Exit Price} - \\text{Skinport HKD Price} \\times \\text{cny\\_per\\_hkd}}{\\text{Skinport HKD Price} \\times \\text{cny\\_per\\_hkd}}$$

&#x20; $$\\text{Instant Exit Price} = \\text{UU Buy Price}$$

&#x20; $$\\text{Instant Profit Rate} = \\frac{\\text{Instant Exit Price} - \\text{Skinport HKD Price} \\times \\text{cny\\_per\\_hkd}}{\\text{Skinport HKD Price} \\times \\text{cny\\_per\\_hkd}}$$

&#x20; Do not embed or hardcode any automatic fee deductions in the code. All listed prices must reflect the raw minimum listed price from the platforms. The user will judge fees manually.



\## 3. Rate Limiting \& Anti-Bot Stealth (Critical)

\*\*Surgical execution with defensive timing. Never spam endpoints.\*\*

\- \*\*Domestic Anti-Bot (悠悠有品):\*\* Every loop or batch request targeting domestic platforms MUST implement a randomized delay using `time.sleep(random.uniform(3, 7))`. Never write tight loops without sleep.

\- \*\*Session Preservation:\*\* Keep headers and cookies minimal and precise. When editing client files (`uu\_client.py`), do not modify or "cleanup" the raw header strings provided by the user.



\## 4. Goal-Driven Vibe Workflows

\*\*Define success criteria via local JSON/Excel artifacts. Loop until verified.\*\*



Transform the project's Sprints into verifiable local milestones:

\- \*\*Sprint 1 (Skinport Fetch):\*\* Run script with `currency=HKD` -> verify `data/skinport\_raw.json` exists and contains valid float numbers for HKD prices.

\- \*\*Sprint 2 (Aggregated Domestic Fetch):\*\* 

&#x20; - 策略：不直接请求悠悠有品 App 接口。改用浏览器 F12 抓取国内饰品聚合平台（如 CSQAQ 网页端）的行情列表接口。

&#x20; - 验证指标：运行脚本 -> 成功在 `data/uu\_raw.json`（或 `data/domestic\_aggregated.json`）中捞出带有悠悠有品/BUFF 当前最新人民币售价的数据。

\- \*\*Sprint 3 (Merge \& Analyze):\*\* Run analyzer -> verify `output/profit\_report.xlsx` is generated, displaying raw un-deducted profit margins using the startup-fetched `cny_per_hkd` HKD-to-RMB rate.

\- \*\*Sprint 4 (Alert Loop):\*\* Run main loop -> simulate alert trigger -> verify Webhook payload dispatches without crashing.



\*\*Robust Error Isolation:\*\* Wrap network requests and dictionary parsing in `try-except` blocks. If one skin contains corrupt data, log it and `continue` the loop—\*\*never allow the daemon to crash.\*\*

