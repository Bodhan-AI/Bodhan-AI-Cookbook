# API verification — November 17, 2026

Reviewed the [official hosted API reference](https://console.bodhan.ai/api-docs/): translation uses `/translate`, with a required target language and optional `max_output_tokens`; the response contains `translation`, `truncated` and usage. No source-language field is supported. Speech uses `/v1/audio/speech`, with model, input, voice and JSON-string instructions. ASR uses multipart `/v1/audio/transcriptions`, with model, language and file; requests are limited to 30 seconds. These schemas inform the adapters; this review is not a live endpoint smoke test.

The [official Indic-Speak model card](https://huggingface.co/bodhan-ai/indic-speak) identifies 13 production languages: English, Hindi, Bengali, Marathi, Telugu, Tamil, Gujarati, Kannada, Malayalam, Odia, Punjabi, Assamese and Urdu. The harness accepts all their ISO codes and the hosted preview languages. Hosted voice selection follows the API reference, which takes precedence over open-weight speaker lists.

Tariff estimates follow the assignment: INR 0.20/10,000 translation output tokens, INR 6/10,000 speech input characters and INR 0.10/audio minute. Caller-supplied judge tariffs are separate. Recheck actual billing before benchmarking.
