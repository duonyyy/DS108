# VOZ Comments Dataset

Dataset prepared from public VOZ thread comments crawled into `data/raw/voz_comments.csv`.

## Files

- `comments.csv`: cleaned tabular dataset.
- `comments.jsonl`: same records in JSON Lines format.
- `threads.csv`: one row per thread.
- `splits/train.jsonl`, `splits/val.jsonl`, `splits/test.jsonl`: deterministic split by thread.
- `metadata.json`: generation summary.

## Schema

- `comment_id`: stable hash id for a comment.
- `thread_id`: stable hash id for a thread.
- `title`, `thread_url`: thread metadata.
- `post_id`: VOZ post id when available.
- `username`: public display name.
- `datetime`: original datetime string.
- `datetime_utc`: parsed UTC datetime when parseable.
- `page`: crawled page number. This project is configured to keep only page 1 per thread.
- `comment`: cleaned comment text.
- `comment_length_chars`, `comment_length_words`: simple text length features.
- `source`: source hostname.

## Summary

- Raw rows: 1053
- Kept rows: 1028
- Threads: 59
- Dropped empty comments: 25
- Dropped duplicates: 0

## Caveats

This dataset is unlabeled. Do not treat it as sentiment, topic, toxicity, or quality training data until labels are added and audited.
Usernames and public post URLs are retained, so consider anonymization before sharing outside your own analysis workflow.
