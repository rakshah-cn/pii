"""Honest latency benchmark for the model path.
   python -m layer1_detection.compare.latency_bench torch            # fp32 PyTorch, one message at a time + batches
   python -m layer1_detection.compare.latency_bench export           # export ONNX fp32 + int8 (models/chitragupta_onnx/)
   python -m layer1_detection.compare.latency_bench int8             # benchmark ONNX int8 + agreement vs fp32 spans
Timing INCLUDES tokenisation, windowing, model, decoding and Chitragupta's own rule post-processing.
Texts: real-looking turns from data/hard_dev.jsonl (synthetic)."""
import json
import os
import statistics
import sys
import time
import types

import torch

sys.path.insert(0, "models/chitragupta")
from chitragupta import Chitragupta   # noqa: E402

ONNX_DIR = "models/chitragupta_onnx"


def sample_texts(n=300):
    rows = [json.loads(l) for l in open("data/hard_dev.jsonl", encoding="utf-8")]
    seen, out = set(), []
    for r in rows:
        for t in r["turns"]:
            if t["text"] not in seen and len(t["text"]) > 3:
                seen.add(t["text"]); out.append(t["text"])
    return out[:n]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def bench(fn, texts, label, batch=1, warm=5):
    for t in texts[:warm]:
        fn([t])
    lat = []
    if batch == 1:
        for t in texts:
            t0 = time.perf_counter(); fn([t]); lat.append((time.perf_counter() - t0) * 1000)
    else:
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            t0 = time.perf_counter(); fn(chunk); lat.append((time.perf_counter() - t0) * 1000 / len(chunk))
    print(f"{label:<34} n={len(lat):<4} p50 {pct(lat,50):7.1f}  p95 {pct(lat,95):7.1f}  p99 {pct(lat,99):7.1f}  mean {statistics.mean(lat):7.1f} ms/msg", flush=True)
    return lat


class OrtShim:
    """Looks like the HF model to Chitragupta.detect_batch but runs an ONNX Runtime session."""
    def __init__(self, path, config, threads):
        import onnxruntime as ort
        so = ort.SessionOptions(); so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.config = config

    def __call__(self, input_ids, attention_mask):
        lg = self.sess.run(["logits"], {"input_ids": input_ids.numpy().astype("int64"),
                                        "attention_mask": attention_mask.numpy().astype("int64")})[0]
        return types.SimpleNamespace(logits=torch.from_numpy(lg))


def main():
    mode = sys.argv[1]
    texts = sample_texts()
    print(f"{len(texts)} texts, mean {statistics.mean(len(t) for t in texts):.0f} chars; cores={os.cpu_count()} torch threads={torch.get_num_threads()}")
    if mode == "torch":
        m = Chitragupta("models/chitragupta", device="cpu")
        bench(m.detect_batch, texts, "torch fp32, 1 msg at a time")
        bench(m.detect_batch, texts, "torch fp32, batch 8", batch=8)
        short = [t for t in texts if len(t) < 30]; longer = [t for t in texts if len(t) >= 80]
        bench(m.detect_batch, short, f"torch fp32, short turns (<30 ch) n={len(short)}")
        bench(m.detect_batch, longer, f"torch fp32, long turns (>=80 ch) n={len(longer)}")
        for th in (4, 8):
            torch.set_num_threads(th)
            bench(m.detect_batch, texts[:120], f"torch fp32, {th} threads, 1 at a time")
    elif mode == "export":
        from transformers import AutoModelForTokenClassification
        os.makedirs(ONNX_DIR, exist_ok=True)
        model = AutoModelForTokenClassification.from_pretrained("models/chitragupta").eval()
        ids = torch.randint(5, 1000, (1, 24)); mask = torch.ones_like(ids)
        class Wrap(torch.nn.Module):
            def __init__(s, m): super().__init__(); s.m = m
            def forward(s, input_ids, attention_mask): return s.m(input_ids=input_ids, attention_mask=attention_mask).logits
        fp32 = os.path.join(ONNX_DIR, "model_fp32.onnx")
        torch.onnx.export(Wrap(model), (ids, mask), fp32, input_names=["input_ids", "attention_mask"], output_names=["logits"],
                          dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"}, "logits": {0: "b", 1: "s"}},
                          opset_version=17, dynamo=False)
        print("exported fp32", flush=True)
        from onnxruntime.quantization import QuantType, quantize_dynamic
        quantize_dynamic(fp32, os.path.join(ONNX_DIR, "model_int8.onnx"), weight_type=QuantType.QInt8, use_external_data_format=True)
        print("quantised int8", flush=True)
    elif mode == "int8":
        m = Chitragupta("models/chitragupta", device="cpu")
        ref = m.detect_batch(texts[:150])
        threads = int(sys.argv[2]) if len(sys.argv) > 2 else torch.get_num_threads()
        for name in ("model_fp32.onnx", "model_int8.onnx"):
            p = os.path.join(ONNX_DIR, name)
            if not os.path.exists(p):
                print("missing", p); continue
            m.model = OrtShim(p, m.model.config, threads)
            bench(m.detect_batch, texts, f"onnx {name[6:-5]}, 1 msg at a time")
            bench(m.detect_batch, texts, f"onnx {name[6:-5]}, batch 8", batch=8)
            got = m.detect_batch(texts[:150])
            same = sum(1 for a, b in zip(ref, got) if {(s['start'], s['end']) for s in a} == {(s['start'], s['end']) for s in b})
            print(f"   span agreement with torch fp32 on 150 texts: {same}/150 identical span sets", flush=True)


main()
