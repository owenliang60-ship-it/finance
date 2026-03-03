"""
Terminal — 未来资本 AI 交易台编排层

统一入口，串联 Data / Knowledge / Portfolio / Risk / Trading 各 Desk。
Claude IS the analyst — pipeline 生成结构化 prompt + 注入数据上下文。
"""
from terminal.company_db import get_company_dir, get_company_record, list_all_companies
from terminal.pipeline import collect_data, prepare_lens_prompts
from terminal.commands import analyze_ticker, portfolio_status, position_advisor, company_lookup
