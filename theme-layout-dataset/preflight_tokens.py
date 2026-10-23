"""CPU-only real processor check; no model weights or training are loaded."""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--processor-model', required=True)
    parser.add_argument('--max-length', type=int, default=8192)
    parser.add_argument('--max-pixels', type=int, default=200704)
    parser.add_argument('--out', help='Optional report path; use a new path on the server to preserve the sealed local report')
    args = parser.parse_args()
    root = Path(args.bundle).resolve(); processor_path = str(Path(args.processor_model).resolve())
    os.chdir(root)
    sys.path.insert(0, str(root / 'code' / 'src'))
    from transformers import AutoProcessor
    import torch
    from multires_app.learning.qwen_runner import messages
    processor = AutoProcessor.from_pretrained(processor_path, local_files_only=True)
    processor.image_processor.size = {'shortest_edge': 4096, 'longest_edge': args.max_pixels}
    records, errors = [], []
    for split in ('train', 'validation', 'test'):
        with (root / (split + '.jsonl')).open(encoding='utf8') as stream:
            for number, line in enumerate(stream):
                if not line.strip(): continue
                try:
                    example = json.loads(line); conversation = messages(example)
                    prefix = processor.apply_chat_template(conversation[:-1], tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt')
                    full = processor.apply_chat_template(conversation, tokenize=True, add_generation_prompt=False, return_dict=True, return_tensors='pt')
                    before = prefix['input_ids'].shape[-1]; total = full['input_ids'].shape[-1]
                    if not torch.equal(prefix['input_ids'][0], full['input_ids'][0, :before]): raise ValueError('assistant mask prefix mismatch')
                    if total > args.max_length: raise ValueError(f'{total} tokens exceeds budget')
                    if total <= before: raise ValueError('empty assistant label')
                    records.append({'split': split, 'line': number + 1, 'tokens': total, 'assistantTokens': total-before})
                except Exception as exc: errors.append({'split': split, 'line': number + 1, 'error': str(exc)})
    result = {'passed': bool(records) and not errors, 'samples': len(records), 'errors': errors,
              'maxTokens': max((r['tokens'] for r in records), default=0), 'maxLength': args.max_length,
              'maxPixels': args.max_pixels, 'processorModel': processor_path,
              'minimumAssistantTokens': min((r['assistantTokens'] for r in records), default=0),
              'scope': 'real image processor, token budget and assistant mask; CPU only; no training/model weights', 'records': records}
    report = Path(args.out).resolve() if args.out else root / 'token-preflight.json'
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'}, ensure_ascii=False), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf8')
    raise SystemExit(main())
