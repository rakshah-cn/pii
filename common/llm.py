"""Tiny Bedrock client. Reads secrets from .env (never printed). Only placeholder text goes here."""
import os
import pathlib


def load_env(path=None):
    p = pathlib.Path(path or pathlib.Path(__file__).resolve().parent.parent / ".env")
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
REGION = os.environ.get("AWS_REGION", "ap-southeast-2")


def client(service="bedrock-runtime"):
    import boto3
    return boto3.client(service, region_name=REGION)


def ask(prompt, model_id, system=None, max_tokens=800, temperature=0.7):
    kw = dict(modelId=model_id, messages=[{"role": "user", "content": [{"text": prompt}]}],
              inferenceConfig={"maxTokens": max_tokens, "temperature": temperature})
    if system:
        kw["system"] = [{"text": system}]
    r = client().converse(**kw)
    return r["output"]["message"]["content"][0]["text"]
