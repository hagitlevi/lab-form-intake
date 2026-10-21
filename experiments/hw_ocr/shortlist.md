# Shortlist: local handwritten-value OCR (research, 2026-10-22)

Constraints: local CPU only; installs on Python 3.13 on Windows and Linux; model RAM <= ~700MB;
open license; < 2 s per crop. Everything below marked "unverified" is from web docs and gets
verified by real install + measurement in Phase 3.

## Shortlisted (6)

| # | Engine | Hebrew printed | Hebrew handwriting | Handwritten digits | Size | License | Py3.13 (dry-run on Windows) |
|---|--------|---|---|---|---|---|---|
| 1 | TrOCR `microsoft/trocr-small-handwritten` | no (English IAM) | no | likely OK on digits (IAM has digits), unverified | ~62M params (~250MB fp32) | MIT | torch 2.14 + transformers resolve |
| 2 | TrOCR `microsoft/trocr-base-handwritten` | no | no | as above | ~334M params (~1.3GB fp32) | MIT | same |
| 3 | Mishkefet-v1 (`itayinbarr/heb-ocr`, weights `itayinbar/Mishkefet-v1`) | n/a | **yes**, line CTC, 0.175 median CER on the ivrit.ai handwriting leaderboard | not stated | 30.2M params (~120MB) | code MIT, **weights CC-BY-NC-SA-4.0** | PyTorch; version not stated |
| 4 | `Rivok/paddleocr-hebrew` (ONNX, PaddleOCR v3.7 finetune) | **yes** (0.35% CER clean print, self-reported) | not claimed | not stated | light 28+27MB, server 77+72+27MB | Apache-2.0 | onnxruntime resolves; Python version not stated |
| 5 | PaddleOCR PP-OCRv5 (`paddleocr` + `paddlepaddle`) | no Hebrew in the multilingual list I could confirm | no | claims English/Chinese handwriting support | ~0.07B params, tens of MB | Apache-2.0 | paddlepaddle 3.3.1 and paddleocr resolve |
| 6 | EasyOCR (`he` + `en`) | listed as supported languages, unverified | no (handwriting is roadmap only) | weak, unverified | ~100MB | Apache-2.0 | easyocr 1.7.2 + torch resolve |

## Rejected during research

- **Kraken**: docs say Linux/macOS only, and `pip install --dry-run` on Windows + Python 3.13 fails with
  `ResolutionImpossible` for every version. Fails the Windows + 3.13 constraint. No Hebrew model found either.
- **Surya**: ~650M-param VLM, over the RAM limit; Hebrew not in any language list I found.
- **docTR**: Hebrew only as a vocabulary entry, no confirmed pretrained Hebrew recognizer; pulls TensorFlow/Keras.
- **Small digit-only recognizer**: no pretrained, licensed, multi-digit handwritten ONNX model exists. Only recipes
  (CRNN+CTC on concatenated MNIST), which means training our own. Out of scope here; revisit if 1 to 6 all fail on digits.

## Notes that matter for the decision

- **Mishkefet is the only real Hebrew-handwriting lead**, but its weights are non-commercial (CC-BY-NC-SA). Per the hard
  constraint it fails on license, so I will still measure it and label the result "license-rejected, informational".
  The README says retraining with `--real-hebrew pinkas` gives permissive weights, but that needs a GPU and days, not a quick fix.
- TrOCR base will probably fail the RAM limit; measured anyway.
- Hebrew handwriting datasets on Hugging Face (sivan22/hebrew-handwritten-dataset etc.) are isolated letters, not usable as a recognizer.

## Sources

- TrOCR overview and sizes: https://playground.roboflow.com/models/microsoft/trocr , https://www.promptlayer.com/models/trocr-base-handwritten
- heb-ocr / Mishkefet-v1: https://github.com/itayinbarr/heb-ocr
- Rivok/paddleocr-hebrew: https://huggingface.co/Rivok/paddleocr-hebrew
- PP-OCRv5: https://huggingface.co/blog/baidu/ppocrv5 , https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
- Kraken: https://kraken.re/6.0.0/index.html
- Surya: https://linuxlinks.com/surya-multilingual-document-ocr-toolkit/
- docTR: https://deepwiki.com/mindee/doctr
- EasyOCR: https://github.com/jaidedai/easyocr
- Digit-string CRNN recipe: https://github.com/kingyiusuen/handwritten-multi-digit-number-recognition
- Hebrew handwriting datasets: https://huggingface.co/datasets/sivan22/hebrew-handwritten-dataset
