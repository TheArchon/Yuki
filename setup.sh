#!/usr/bin/env bash
set -e
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp -n .env.example .env || true
echo "Edit .env, then run: source venv/bin/activate && python3 main.py"
