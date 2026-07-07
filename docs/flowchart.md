# End-to-end process flowchart

How a scrape run flows from configuration to the dashboard. GitHub renders
this diagram automatically; the same picture in words is in the README's
Architecture section.

```mermaid
flowchart TD

subgraph CONFIG["1 · Configuration"]
    A["config.yaml<br/>pincodes · search terms · platform on/off<br/>rate limits · retries · proxy"]
end

subgraph RUN["2 · Scrape sweep — python main.py (manual or scheduled)"]
    B["Start sweep"] --> C{"For each enabled platform<br/>(Blinkit, Instamart, Zepto,<br/>BB Now, Amazon Now, Flipkart Min.)"}
    C --> D["Launch Chromium via Playwright<br/>rotating user-agent · optional proxy"]

    D --> E{"For each pincode"}
    E --> F["set_location(pincode)<br/>drive the site's real location picker"]
    F -->|"failed (UI changed /<br/>area not serviceable)"| G["Log error · skip this pincode"]
    G --> E
    F -->|"location set"| H{"For each search term"}

    H --> I["Rate limiter: wait min-delay + jitter<br/>(per domain)"]
    I --> J["Open the search page<br/>retry with backoff: 2s → 4s → 8s"]
    J --> K["Site's own JavaScript calls its<br/>internal JSON API (reverse-engineered)"]
    K --> L["Interceptor captures matching API<br/>responses — no fragile HTML parsing<br/>(exception: Amazon adds a DOM fallback)"]
    L -->|"nothing captured<br/>(endpoint changed?)"| M["Log error · continue<br/>→ see README maintenance checklist"]
    M --> H
    L -->|"JSON captured"| N["Parse payload defensively"]

    N --> O["Normalize to common schema:<br/>platform · name · brand · price · mrp<br/>discount% · pack size · in_stock<br/>stock_estimate + granularity flag<br/>(boolean / estimate / count)"]
    O --> P[("Append timestamped rows to<br/>SQLite — data/observations.db")]
    P --> H
    H -->|"terms done"| E
    E -->|"pincodes done"| Q["Close browser"]
    Q --> C
end

subgraph TIME["3 · Over time"]
    R["Scheduler re-runs the sweep<br/>(cron / Windows Task Scheduler)"]
    S[("Database grows into a<br/>price & stock time series")]
end

subgraph VIEW["4 · Dashboard — python dashboard.py"]
    T["Local web server<br/>JSON API over the SQLite db"]
    U["Browser page — http://127.0.0.1:8000<br/>filters · stat tiles · price-by-platform chart<br/>snapshot table · per-product history"]
end

A --> B
C -->|"platforms done"| S
R --> B
P -.-> S
S --> T
T --> U

CLICK["You: pick product/pincode/date range,<br/>click a row for its full history"] --> U
```
