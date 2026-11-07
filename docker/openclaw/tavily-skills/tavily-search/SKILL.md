---
name: tavily-search
metadata: {"openclaw":{"requires":{"env":["TAVILY_API_BASE","TAVILY_API_KEY"]}}}
description: Search the web for current or verifiable information using the Tavily Search API through the MaraClaw engine proxy.
---

# Tavily Search

Tavily Search returns ranked web results (title, URL, content snippet, relevance score) and can optionally add an LLM-written answer. You read the results and do the synthesis yourself.

## How to call it

MaraClaw supplies `TAVILY_API_BASE` (the engine proxy base, without `/v1`) and `TAVILY_API_KEY` (an agent-scoped proxy token, not a Tavily account key). Send Tavily requests only to this proxy.

POST JSON to `$TAVILY_API_BASE/v1/search`. `query` is the only required field.

```shell
curl -sS -X POST "$TAVILY_API_BASE/v1/search" \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"query":"Who is the CEO of Stripe?"}'
```

The response holds `results[]` (`title`, `url`, `content`, `score`), plus `answer` when requested. Keep each claim tied to the `url` it came from.

## Choose the request shape

Add optional fields only when they change the outcome.

| Need | Field |
| --- | --- |
| Balanced default | `search_depth: "basic"` (omit it) |
| Detailed, high-precision lookup | `search_depth: "advanced"` (costs 2 credits) |
| Low latency | `search_depth: "fast"` or `"ultra-fast"` |
| Breaking news or recent events | `topic: "news"` (finance: `"finance"`) |
| Only recent sources | `time_range: "day" \| "week" \| "month" \| "year"` or `start_date` / `end_date` (`YYYY-MM-DD`) |
| Restrict or drop source families | `include_domains` / `exclude_domains` (lists of domains) |
| A quick synthesized answer | `include_answer: true` (`"advanced"` for more detail) |
| Full page text for each result | `include_raw_content: "markdown"` |
| More or fewer results | `max_results` (0-20, default 10) |
| Exact phrase match | quote the phrase in `query` and set `exact_match: true` |

Example with filters:

```shell
curl -sS -X POST "$TAVILY_API_BASE/v1/search" \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"query":"latest funding round Acme Corp","topic":"news","time_range":"month","max_results":5,"include_answer":true}'
```

## Write the query

- Make it specific and self-contained: name the entity, the fact wanted, and the timeframe. Prefer `"Acme Corp Series B funding amount 2026"` over `"tell me about their funding"`.
- Keep it short (under about 400 characters). Split a multi-part question into separate searches instead of one long query.
- Set domain filters only when the source family is actually implied. Never invent domains.
- If snippets are not enough, take the best `url` and use the `tavily-crawl` skill to retrieve content from that page and linked pages.

## Handle failures

A non-200 response carries `detail.error`. `401` means the proxy token is missing or invalid. `429` is rate limiting: wait for `Retry-After` seconds if present, then retry once. `432` and `433` mean a usage or plan limit was hit, so stop and tell the user rather than retrying.

For exact parameters, defaults, and response fields read `references/TAVILY_SEARCH_API_REFERENCE.md` in this skill's directory.
