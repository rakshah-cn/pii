"""Does int8 hurt recall?   python -m layer1_detection.compare.int8_recall data/hard_dev.jsonl 150"""
import json, sys
from layer0_data import scoreboard as sb
from layer1_detection.detector import Detector
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")][:int(sys.argv[2])]
for name, det in (("torch fp32", Detector()), ("onnx int8", Detector(model_backend="onnx_int8"))):
    preds, ms = sb.run(det, rows)
    cells, nh, nt, fpf, fc, fpl, src, misses = sb.score(rows, preds)
    a = cells[("ALL",)]
    print(f"{name:11} masked {100*a[1]/a[0]:.1f}%  full {100*a[2]/a[0]:.1f}%  strict {100*a[3]/a[0]:.1f}%  NAME full "
          f"{100*cells[('type','NAME')][2]/cells[('type','NAME')][0]:.1f}%  PII-free FA {fpf}/{fc}  misses {len(misses)}  ({ms:.1f} ms/turn batched)")
