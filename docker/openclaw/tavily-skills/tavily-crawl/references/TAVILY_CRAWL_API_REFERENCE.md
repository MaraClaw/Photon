# Tavily Crawl API Reference

## Request

```shell
curl -sS -X POST "$TAVILY_API_BASE/v1/crawl" \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/docs","max_depth":1,"limit":5,"allow_external":false,"timeout":30}'
```

`TAVILY_API_BASE` is the proxy base without `/v1`; `TAVILY_API_KEY` is the guest proxy token sent as a Bearer token. The JSON body requires one root `url` string.

## Parameters

| Field | Type / values | Default | Notes |
| --- | --- | --- | --- |
| `url` | string | required | Root URL to crawl. |
| `instructions` | string | none | Natural-language guidance for traversal and content relevance. |
| `chunks_per_source` | integer 1-5 | 3 | Only when `instructions` is present. |
| `max_depth` | integer 1-5 | 1 | Maximum crawl depth. |
| `max_breadth` | integer 1-500 | 20 | Maximum links followed per page. |
| `limit` | integer >= 1 | 50 | Maximum number of links processed. |
| `select_paths`, `exclude_paths` | string[] | none | Regex patterns to include or exclude URL paths. |
| `select_domains`, `exclude_domains` | string[] | none | Regex patterns to include or exclude domains. |
| `allow_external` | boolean | true | Permit external links; set false for a scoped crawl. |
| `include_images` | boolean | false | Include images. |
| `extract_depth` | `basic`, `advanced` | `basic` | Advanced retrieves more content but may increase latency. |
| `format` | `markdown`, `text` | `markdown` | Content format. |
| `include_favicon` | boolean | false | Include favicon URLs. |
| `timeout` | number 10-150 seconds | 150 | Request timeout; the proxy has its own deadline and may end sooner. |
| `include_usage` | boolean | false | Include usage information. |

## Response (200)

The response is synchronous; it contains results directly rather than a job identifier to poll.

```json
{
  "base_url": "https://example.com/docs",
  "results": [{"url": "https://example.com/docs", "raw_content": "Example documentation."}],
  "response_time": 1.2,
  "request_id": "123e4567-e89b-12d3-a456-426614174111"
}
```

Each result contains `url` and `raw_content`, with `favicon` when requested. `usage` may be included when requested. `response_time` is in seconds; `request_id` identifies the request.

## Errors

| Status | Meaning | Action |
| --- | --- | --- |
| 400 | Missing or invalid root URL | Fix the request. |
| 401 | Missing or invalid token | Stop and report. |
| 403 | Unsupported URL | Choose a supported URL. |
| 429 | Rate limited | Honor `Retry-After` if present; retry once. |
| 432, 433 | Usage or plan limit exceeded | Stop and report. |
| 500 | Server error | Retry once, then report. |
