---
name: tavily-crawl
metadata: {"openclaw":{"requires":{"env":["TAVILY_API_BASE","TAVILY_API_KEY"]}}}
description: Crawl a root URL and retrieve linked page content using the Tavily Crawl API through the MaraClaw engine proxy.
---

# Tavily Crawl

MaraClaw supplies `TAVILY_API_BASE` (the engine proxy base without `/v1`) and `TAVILY_API_KEY` (the guest proxy token, not a Tavily account key). Send requests only to this proxy.

POST JSON to `$TAVILY_API_BASE/v1/crawl` with the required singular `url`. Use a modest limit and timeout to fit the proxy's configured deadline.

```shell
curl -sS -X POST "$TAVILY_API_BASE/v1/crawl" \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/docs","max_depth":1,"limit":5,"allow_external":false,"timeout":30}'
```

The response is synchronous, not a job: `base_url` identifies the root and `results[]` contains each page's `url` and `raw_content`. `response_time` and `request_id` are also returned. An empty results array contains no page content to cite.

Control traversal with `max_depth`, `max_breadth`, `limit`, and the path or domain filters. Set `instructions` to guide the crawl; `chunks_per_source` applies only when instructions are supplied. Use `extract_depth: "advanced"` for harder pages and `format: "text"` for plain text. See `references/TAVILY_CRAWL_API_REFERENCE.md` for the full contract.

On `401` report the invalid proxy token; on `429` honor `Retry-After` if present and retry once; on `432` or `433` stop and report the usage limit. Keep page URLs with the facts they support.
