@echo off
cd /d C:\Foundry\rexfinhub
python -m streamlit run tools/rules_editor/app.py %*
