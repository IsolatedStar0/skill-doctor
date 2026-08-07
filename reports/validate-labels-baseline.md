# Skill Doctor Label Validation

- Label set: `diagnostic_cases/puck-rule-rca-labeled-0715.jsonl`
- Domain quality threshold: `0.75`
- Total: `53`
- Skipped: `1`
- Prediction accuracy: 88.7%
- Quality accuracy: 96.2%
- False accept rate: 3.8%

## Filter Decision Confusion

| TP | TN | FP | FN |
| ---: | ---: | ---: | ---: |
| 47 | 0 | 6 | 0 |

## Domain Quality Confusion

| True Accept | True Reject | False Accept | False Reject |
| ---: | ---: | ---: | ---: |
| 47 | 4 | 2 | 0 |

## Quality Gate

- Passed: `True`

## Worst Domain Quality Cases

| Case | Expected | Predicted | Correct | Domain Score | Quality Pass |
| --- | --- | --- | --- | ---: | --- |
| `puck-rule-rca-0715-12432624` | False | True | False | 0.62 | False |
| `puck-rule-rca-0715-12369560` | False | True | False | 0.64 | False |
| `puck-rule-rca-0715-12417467` | False | True | False | 0.7 | False |
| `puck-rule-rca-0715-12309736` | False | True | False | 0.7 | False |
| `puck-rule-rca-0715-12400979` | True | True | True | 0.92 | True |
| `puck-rule-rca-0715-12452048` | True | True | True | 0.92 | True |
| `puck-rule-rca-0715-12452526` | True | True | True | 0.92 | True |
| `puck-rule-rca-0715-12452599` | True | True | True | 0.92 | True |
| `puck-rule-rca-0715-12443228` | True | True | True | 0.92 | True |
| `puck-rule-rca-0715-12417993` | True | True | True | 1.0 | True |
