# Tavily Search API Reference

## Request

```shell
curl -sS -X POST "$TAVILY_API_BASE/v1/search" \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"query":"Who is the CEO of Stripe?","max_results":5}'
```

- `TAVILY_API_BASE` is the proxy base without `/v1`; the path is appended as shown.
- `TAVILY_API_KEY` is the agent-scoped proxy token sent as a Bearer header. It is not a Tavily account key.
- Body is JSON. `query` (string) is required.

## Parameters

| Field | Type / values | Default | Notes |
| --- | --- | --- | --- |
| `query` | string | required | The search query. |
| `search_depth` | `basic`, `advanced`, `fast`, `ultra-fast` | `basic` | `advanced` is highest relevance and costs 2 credits; the others cost 1. `ultra-fast` returns one summary per URL. |
| `chunks_per_source` | 1-3 | 3 | Snippets per source; for `basic`, `fast`, `advanced`. |
| `max_results` | 0-20 | 10 | Number of results. |
| `topic` | `general`, `news`, `finance` | `general` | `news` suits recent events and also returns `published_date`. |
| `time_range` | `day`, `week`, `month`, `year` (or `d`, `w`, `m`, `y`) | none | Filters by publish or last-updated date. |
| `start_date`, `end_date` | `YYYY-MM-DD` | none | Date window. |
| `include_published_date` | boolean | false | Adds `published_date` to each result (beta). |
| `filter_by_published_date` | boolean | false | Drops results outside the date window, including undated ones. |
| `include_answer` | boolean, `basic`, `advanced` | false | LLM-written answer to the query. |
| `include_raw_content` | boolean, `markdown`, `text` | false | Full cleaned page content per result. |
| `include_images`, `include_image_descriptions` | boolean | false | Image URLs, optionally with descriptions. |
| `include_favicon` | boolean | false | Favicon URL per result. |
| `include_domains` | string[] (max 300) | [] | Restrict results to these domains. |
| `exclude_domains` | string[] (max 150) | [] | Drop these domains. |
| `include_domains_mode` | `restrict`, `prefer` | `restrict` | Needs `include_domains`. |
| `country` | lowercase country name, e.g. `germany` | none | Boosts that country; only for `topic: general`. |
| `language`, `filter_by_language` | ISO 639-1 code or English name; boolean | none, false | `filter_by_language` needs `language`. |
| `auto_parameters` | boolean | false | Tavily picks parameters from the query; may use 2 credits. |
| `exact_match` | boolean | false | Require the quoted phrase(s) in `query`. |
| `include_usage` | boolean | false | Adds `usage.credits`. |
| `safe_search` | boolean | false | Not supported for `fast` or `ultra-fast`. |

## Response (200)

| Field | Meaning |
| --- | --- |
| `query` | The query that was run. |
| `answer` | Present when `include_answer` was requested. |
| `results[]` | Ranked by relevance: `title`, `url`, `content`, `score`, plus `raw_content`, `published_date`, `favicon`, `images`, `id` when requested. |
| `images[]` | Query-related images when `include_images` is true. |
| `response_time` | Seconds taken. |
| `usage` | Credit usage when `include_usage` is true. |
| `request_id` | Identifier to quote when reporting a problem. |

## Errors

Error bodies look like `{"detail":{"error":"..."}}`.

| Status | Meaning | Action |
| --- | --- | --- |
| 400 | Invalid request (for example a bad `topic`) | Fix the request. |
| 401 | Missing or invalid token | Stop and report. |
| 422 | Field failed validation; `detail` is an array | Fix the named field. |
| 429 | Rate limited; may include `Retry-After` | Wait, retry once. |
| 432, 433 | Key, plan, or pay-as-you-go limit exceeded | Stop and report. |
| 500 | Server error | Retry once, then report. |

## Practices

- Use `include_raw_content` sparingly; it enlarges the response.
