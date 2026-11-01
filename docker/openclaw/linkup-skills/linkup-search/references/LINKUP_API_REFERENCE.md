# Linkup API Context for Coding Agents

## What is Linkup?

Linkup is a web search API built specifically for AI applications and agents.

It provides:

- Real-time web search

- Citation-backed answers

- Web page fetching

- Structured JSON outputs

- Domain filtering and source control

Think of Linkup as **internet access for LLMs and AI agents**.

---

# Core APIs

## 1. Search API

Search the live web and return:

- Search results (URLs + snippets)

- Citation-backed natural language answers

- Structured JSON matching a schema

### Endpoint

```shell

curl -sS -X POST "$LINKUP_API_BASE/v1/search" \
  -H "Authorization: Bearer $LINKUP_PROXY_TOKEN" -H "Content-Type: application/json" \
  -d '{"q":"Who is the CEO of Stripe?","depth":"standard","outputType":"sourcedAnswer"}'

```

### Typical use cases

- Agent web search

- Retrieval Augmented Generation (RAG)

- Company lookups

- Lead enrichment

- Market intelligence

- Fact verification

### Example

```json

{

  "query": "Who is the CEO of Stripe?",

  "depth": "standard",

  "outputType": "sourcedAnswer"

}

```

### Output Types

#### searchResults

Returns URLs and snippets.

```json

{

  "outputType": "searchResults"

}

```

Best when:

- Agent wants to inspect sources itself

- Multi-step workflows

---

#### sourcedAnswer

Returns a generated answer with citations.

```json

{

  "outputType": "sourcedAnswer"

}

```

Best when:

- User-facing chat

- Question answering

- Agent reasoning

---

#### structured

Returns JSON matching a user schema.

```json

{

  "outputType": "structured",

  "structuredOutputSchema": {

    ...

  }

}

```

Best when:

- CRM enrichment

- Lead generation

- Data collection

- Workflow automation

---

# Search Depth

## Fast

Sub-second, keyword-only search. No LLM, no scraping, no chaining (beta).

Use for:

- Latency-critical lookups (chat, voice, high-volume pipelines)

- Simple keyword-shaped queries with one target fact

```json

{

  "depth": "fast"

}

```

---

## Standard

Single-iteration agentic search, ~1-3s. Can run parallel sub-searches and
scrape one URL provided in the query.

Use for:

- Most agent searches

- Enrichment

- Simple questions

```json

{

  "depth": "standard"

}

```

---

## Deep

Up to 10 iterations, ~5-30s. Can scrape multiple URLs and chain
search-then-scrape sequentially. Use when the next step depends on the
previous step's output.

Use for:

- Competitive analysis

- Complex investigations

- Multi-source validation

```json

{

  "depth": "deep"

}

```

---

# Structured Output

One of Linkup's most powerful features.

Provide a JSON schema and Linkup returns validated structured data.

Example:

```json

{

  "query": "Find information about OpenAI",

  "outputType": "structured",

  "structuredOutputSchema": {

    "type": "object",

    "properties": {

      "company_name": {

        "type": "string"

      },

      "founding_year": {

        "type": "number"

      },

      "headquarters": {

        "type": "string"

      }

    }

  }

}

```

Possible response:

```json

{

  "company_name": "OpenAI",

  "founding_year": 2015,

  "headquarters": "San Francisco, California"

}

```

---

# Domain Controls

Limit or exclude sources.

## Include Domains

```json

{

  "includeDomains": [

    "wikipedia.org",

    "openai.com"

  ]

}

```

Only search these domains.

---

## Exclude Domains

```json

{

  "excludeDomains": [

    "reddit.com"

  ]

}

```

Ignore specific sources.

---

# Fetch API

Retrieve clean content from any URL.

Useful when an agent already knows which page it wants.

### Endpoint

```shell

curl -sS -X POST "$LINKUP_API_BASE/v1/fetch" \
  -H "Authorization: Bearer $LINKUP_PROXY_TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://openai.com"}'

```

### Capabilities

- HTML parsing

- Markdown conversion

- JavaScript rendering

- PDF support

- Image retrieval

### Example

```json

{

  "url": "https://openai.com"

}

```

Typical workflow:

1. Search for a source

2. Fetch the page

3. Feed content to an LLM

---

# Citations

Linkup returns source citations whenever possible.

Agents should:

- Preserve citations when presenting answers

- Surface URLs to end users

- Use citations for traceability

This is a key advantage over raw LLM generation.

---

# Authentication

MaraClaw automatically supplies `LINKUP_API_BASE` (the engine proxy base, including `/api/linkup`) and the agent-scoped `LINKUP_PROXY_TOKEN`. Use only this proxy for Linkup requests.

Header:

```shell

-H "Authorization: Bearer $LINKUP_PROXY_TOKEN"

```

---

# Recommended Agent Patterns

## Pattern 1: RAG Search

```text

User Question

      ↓

Linkup Search

      ↓

Cited Results

      ↓

LLM Answer

```

---

## Pattern 2: Structured Enrichment

```text

Company Name

      ↓

Linkup Structured Search

      ↓

Validated JSON

      ↓

CRM / Database

```

---

## Pattern 3: Search + Fetch

```text

Search

   ↓

Relevant URL

   ↓

Fetch

   ↓

Page Content

   ↓

LLM

```

---

# When to Use Which Endpoint

| Goal | Endpoint |

|--------|----------|

| Find information on the web | Search |

| Get cited answers | Search |

| Generate structured JSON | Search |

| Get content from a URL | Fetch |

| Crawl a specific page | Fetch |

| Analyze PDFs | Fetch |

---

# Best Practices

1. Use `structured` output whenever data will feed another system.

2. Use `sourcedAnswer` for user-facing experiences.

3. Use `searchResults` when the agent needs full control over reasoning.

4. Use `fetch` when a URL is already known.

5. Preserve citations whenever possible.

6. Prefer domain filtering for high-trust workflows.

7. Treat Linkup as the source of truth for real-time information, not the LLM.

---

# Mental Model

Linkup provides two layers:

```text

Search

  ↓

Fetch

```

Search finds information.

Fetch retrieves information.

Together they give AI agents reliable access to the live web.
