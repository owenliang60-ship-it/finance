#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 晨报 — 量价动量引擎 (Engine A)

替代 daily_scan.py，整合所有动量信号：
A. PMARP 极值
B. 量能加速 (DV Acceleration)
C. RVOL 持续放量
D. Dollar Volume Top 50 + 新面孔
E. 市场情绪脉搏 (Adanos market-level)
F. 社交热门 Top 10 + 热门板块 (Adanos trending)

用法:
    python scripts/morning_report.py                  # 完整晨报
    python scripts/morning_report.py --no-telegram    # 本地测试，不推送
"""

import sys
import time
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    DATA_DIR, SCANS_DIR,
    DOLLAR_VOLUME_REPORT_N, DOLLAR_VOLUME_LOOKBACK,
    DV_ACCELERATION_THRESHOLD, RVOL_SUSTAINED_THRESHOLD,
    EXTENDED_UNIVERSE_MIN_MCAP_B,
)
from src.data import get_symbols
from src.indicators.dv_acceleration import format_dv
from src.telegram_bot import send_message, send_photo, split_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

EXTENDED_LAYER_MIN_MCAP = EXTENDED_UNIVERSE_MIN_MCAP_B * 1_000_000_000
MORNING_SIGNAL_PRICE_ROWS = 180
LAYER_ORDER = ["pool", "extend", "broad"]
LAYER_LABELS = {
    "pool": "Pool",
    "extend": "Extend ($10B+)",
    "broad": "Broad ($1B-$10B)",
}
LAYER_TOP_N = 8

CONCEPT_BUCKET_ORDER = [
    "AI算力/云",
    "半导体链",
    "数据中心电力",
    "通信/网络设备",
    "互联网/广告",
    "软件/SaaS",
    "自动驾驶/机器人",
    "金融/加密",
    "医药/生命科学",
    "工业/航天/国防",
    "消费/电商",
    "能源/材料",
    "地产/基础设施",
    "ETF/宏观工具",
    "其他",
]

THEME_BUCKET_HINTS = {
    "ai_chip": "AI算力/云",
    "ai_software": "AI算力/云",
    "ai_agent": "AI算力/云",
    "ai_infra": "AI算力/云",
    "cloud": "AI算力/云",
    "quantum": "AI算力/云",
    "memory": "半导体链",
    "semicap": "半导体链",
    "chip_design": "半导体链",
    "liquid_cooling": "数据中心电力",
    "nuclear_power": "数据中心电力",
    "cybersecurity": "软件/SaaS",
    "enterprise_sw": "软件/SaaS",
    "autonomous_driving": "自动驾驶/机器人",
    "humanoid_robot": "自动驾驶/机器人",
    "ev_battery": "自动驾驶/机器人",
    "streaming": "互联网/广告",
    "digital_ads": "互联网/广告",
    "fintech": "金融/加密",
    "crypto": "金融/加密",
    "glp1": "医药/生命科学",
    "biotech": "医药/生命科学",
    "space": "工业/航天/国防",
    "defense": "工业/航天/国防",
}

ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "DIA", "SOXX", "SMH", "XLK", "XLF", "XLE", "XLV",
    "XLY", "XLI", "XLC", "EWY", "EWT", "FXI", "KWEB",
}

SYMBOL_BUCKET_OVERRIDES = {
    # AI / cloud platforms and flagship application names.
    "NVDA": "AI算力/云",
    "MSFT": "AI算力/云",
    "GOOG": "AI算力/云",
    "GOOGL": "AI算力/云",
    "AMZN": "AI算力/云",
    "ORCL": "AI算力/云",
    "PLTR": "AI算力/云",
    "IBM": "AI算力/云",
    "ANET": "AI算力/云",
    "SNOW": "AI算力/云",
    "CRWV": "AI算力/云",
    "NBIS": "AI算力/云",
    # Semiconductor supply chain.
    "AMD": "半导体链",
    "AVGO": "半导体链",
    "TSM": "半导体链",
    "ASML": "半导体链",
    "MU": "半导体链",
    "ARM": "半导体链",
    "INTC": "半导体链",
    "MRVL": "半导体链",
    "QCOM": "半导体链",
    "LRCX": "半导体链",
    "AMAT": "半导体链",
    "KLAC": "半导体链",
    "SMCI": "半导体链",
    # Internet, ads, streaming.
    "META": "互联网/广告",
    "NFLX": "互联网/广告",
    "APP": "互联网/广告",
    "RDDT": "互联网/广告",
    "PINS": "互联网/广告",
    "UBER": "互联网/广告",
    "LYFT": "互联网/广告",
    # Software / SaaS.
    "CRM": "软件/SaaS",
    "NOW": "软件/SaaS",
    "ADBE": "软件/SaaS",
    "CRWD": "软件/SaaS",
    "PANW": "软件/SaaS",
    "NET": "软件/SaaS",
    "DDOG": "软件/SaaS",
    "MDB": "软件/SaaS",
    # Autos, robots, electrification.
    "TSLA": "自动驾驶/机器人",
    "RIVN": "自动驾驶/机器人",
    "LCID": "自动驾驶/机器人",
    "XPEV": "自动驾驶/机器人",
    "NIO": "自动驾驶/机器人",
    # Finance / crypto / brokers.
    "COIN": "金融/加密",
    "MSTR": "金融/加密",
    "HOOD": "金融/加密",
    "SOFI": "金融/加密",
    "PYPL": "金融/加密",
    "SQ": "金融/加密",
    "IBKR": "金融/加密",
    "JPM": "金融/加密",
    "GS": "金融/加密",
    # Health care.
    "LLY": "医药/生命科学",
    "NVO": "医药/生命科学",
    "UNH": "医药/生命科学",
    "MRK": "医药/生命科学",
    "VRTX": "医药/生命科学",
    # Industrial / aerospace / defense.
    "RKLB": "工业/航天/国防",
    "ASTS": "工业/航天/国防",
    "BA": "工业/航天/国防",
    "LMT": "工业/航天/国防",
    "GE": "工业/航天/国防",
    # Consumer / commerce.
    "AAPL": "消费/电商",
    "PDD": "消费/电商",
    "BABA": "消费/电商",
    "SHOP": "消费/电商",
    "MELI": "消费/电商",
    "COST": "消费/电商",
    "WMT": "消费/电商",
    "HD": "消费/电商",
    # Energy / materials.
    "XOM": "能源/材料",
    "CVX": "能源/材料",
    "CCJ": "能源/材料",
    "FCX": "能源/材料",
    "ALB": "能源/材料",
    # Current broad-signal fallback coverage when market_db has ticker/cap only.
    "OGN": "医药/生命科学",
    "CAH": "医药/生命科学",
    "INBX": "医药/生命科学",
    "ACLX": "医药/生命科学",
    "ABCL": "医药/生命科学",
    "RCUS": "医药/生命科学",
    "MOH": "医药/生命科学",
    "ERAS": "医药/生命科学",
    "WST": "医药/生命科学",
    "ICLR": "医药/生命科学",
    "GRFS": "医药/生命科学",
    "TFX": "医药/生命科学",
    "MEDP": "医药/生命科学",
    "MXL": "半导体链",
    "AOSL": "半导体链",
    "POWI": "半导体链",
    "VICR": "半导体链",
    "COHU": "半导体链",
    "VECO": "半导体链",
    "PI": "半导体链",
    "SYNA": "半导体链",
    "SNDK": "半导体链",
    "STX": "半导体链",
    "COHR": "通信/网络设备",
    "TEL": "通信/网络设备",
    "KN": "通信/网络设备",
    "CHTR": "通信/网络设备",
    "EXTR": "通信/网络设备",
    "CALX": "通信/网络设备",
    "VZ": "通信/网络设备",
    "GLW": "通信/网络设备",
    "SRAD": "互联网/广告",
    "LBRDK": "互联网/广告",
    "SIRI": "互联网/广告",
    "FICO": "软件/SaaS",
    "FRSH": "软件/SaaS",
    "PEGA": "软件/SaaS",
    "WK": "软件/SaaS",
    "AMSC": "数据中心电力",
    "OKLO": "数据中心电力",
    "ITRI": "数据中心电力",
    "DQ": "能源/材料",
    "ENIC": "能源/材料",
    "WKC": "能源/材料",
    "HLX": "能源/材料",
    "AAUC": "能源/材料",
    "KALU": "能源/材料",
    "CLF": "能源/材料",
    "LPX": "能源/材料",
    "RS": "能源/材料",
    "ROG": "能源/材料",
    "TX": "能源/材料",
    "FLS": "工业/航天/国防",
    "HII": "工业/航天/国防",
    "DRS": "工业/航天/国防",
    "VVX": "工业/航天/国防",
    "SON": "工业/航天/国防",
    "PBI": "工业/航天/国防",
    "AZZ": "工业/航天/国防",
    "HTLD": "工业/航天/国防",
    "SANM": "工业/航天/国防",
    "RDW": "工业/航天/国防",
    "VTOL": "工业/航天/国防",
    "ICFI": "工业/航天/国防",
    "TNC": "工业/航天/国防",
    "URI": "地产/基础设施",
    "BLD": "地产/基础设施",
    "MAS": "地产/基础设施",
    "MGRC": "地产/基础设施",
    "ZWS": "地产/基础设施",
    "IIPR": "地产/基础设施",
    "SILA": "地产/基础设施",
    "QXO": "地产/基础设施",
    "NHI": "地产/基础设施",
    "WSO": "地产/基础设施",
    "STRA": "消费/电商",
    "EDU": "消费/电商",
    "MTN": "消费/电商",
    "CAR": "消费/电商",
    "MCRI": "消费/电商",
    "PENN": "消费/电商",
    "LULU": "消费/电商",
    "BYD": "消费/电商",
    "CHDN": "消费/电商",
    "LVS": "消费/电商",
    "TSCO": "消费/电商",
    "TAL": "消费/电商",
    "ABG": "消费/电商",
    "HCSG": "医药/生命科学",
    "CASH": "金融/加密",
    "NYAX": "金融/加密",
    "EVO": "金融/加密",
    "WU": "金融/加密",
    "WEX": "金融/加密",
    "FBP": "金融/加密",
    "TFIN": "金融/加密",
    "SIGI": "金融/加密",
    "NTRS": "金融/加密",
    "VOYA": "金融/加密",
    "UVE": "金融/加密",
    "SEIC": "金融/加密",
    "RY": "金融/加密",
    "AIG": "金融/加密",
    "OUST": "自动驾驶/机器人",
    "MBLY": "自动驾驶/机器人",
    "VC": "自动驾驶/机器人",
    "GNTX": "自动驾驶/机器人",
    "CTOS": "自动驾驶/机器人",
    "SEMR": "软件/SaaS",
    "PDFS": "半导体链",
    "FORM": "半导体链",
    "NOK": "通信/网络设备",
    "AKO-A": "消费/电商",
    "DPZ": "消费/电商",
    "DLTR": "消费/电商",
    "AZO": "消费/电商",
    "SXT": "能源/材料",
    "LAC": "能源/材料",
    "WES": "能源/材料",
    "SHEL": "能源/材料",
    "EPD": "能源/材料",
    "KRP": "能源/材料",
    "CNQ": "能源/材料",
    "TDW": "能源/材料",
    "KEN": "能源/材料",
    "MOG-B": "工业/航天/国防",
    "RHI": "工业/航天/国防",
    "PLOW": "工业/航天/国防",
    "PATK": "工业/航天/国防",
    "CAE": "工业/航天/国防",
    "TTEK": "工业/航天/国防",
    "SKYW": "工业/航天/国防",
    "WWD": "工业/航天/国防",
    "FSV": "地产/基础设施",
    "EGP": "地产/基础设施",
    "SLM": "金融/加密",
    "LC": "金融/加密",
    "TCBI": "金融/加密",
    "ENVA": "金融/加密",
    "HLNE": "金融/加密",
    "CNOB": "金融/加密",
    "HCXY": "金融/加密",
    "EBC": "金融/加密",
    "BHFAP": "金融/加密",
    "UHS": "医药/生命科学",
}

BUSINESS_ROLE_OVERRIDES = {
    # AI / cloud
    "NVDA": "GPU/AI加速器",
    "MSFT": "云+企业软件",
    "GOOG": "搜索广告+云",
    "GOOGL": "搜索广告+云",
    "AMZN": "AWS云+电商",
    "ORCL": "数据库+云",
    "PLTR": "AI数据平台",
    "IBM": "企业AI/混合云",
    "CRWV": "GPU云算力租赁",
    "AIG": "保险/金融服务",
    "QS": "固态电池",
    # Semiconductors and electronics
    "AMD": "GPU/CPU",
    "AVGO": "ASIC/网络芯片",
    "TSM": "晶圆代工",
    "ASML": "EUV光刻机",
    "MU": "DRAM/HBM存储",
    "INTC": "CPU/晶圆制造",
    "ARM": "芯片IP授权",
    "MRVL": "数据中心连接芯片",
    "QCOM": "手机SoC/基带",
    "LRCX": "刻蚀设备",
    "AMAT": "半导体设备",
    "KLAC": "量检测设备",
    "SMCI": "AI服务器",
    "MXL": "模拟/混合信号芯片",
    "NVTS": "氮化镓功率芯片",
    "AOSL": "功率半导体",
    "TXN": "模拟芯片",
    "POWI": "高压电源芯片",
    "VICR": "电源模块",
    "COHU": "半导体测试设备",
    "RMBS": "内存接口IP",
    "VECO": "薄膜沉积设备",
    "ON": "汽车/功率芯片",
    "PI": "RFID/IoT芯片",
    "STM": "MCU/功率芯片",
    "LSCC": "低功耗FPGA",
    "SYNA": "人机接口芯片",
    "PDFS": "半导体良率软件",
    "FORM": "晶圆探针卡",
    "SNDK": "闪存/存储",
    "STX": "硬盘存储",
    "WDC": "硬盘/闪存存储",
    "COHR": "光通信器件/工业激光",
    # Data center power / physical infra
    "AMSC": "电网超导设备",
    "OKLO": "小型核反应堆",
    "GEV": "电网/燃机设备",
    "VRT": "数据中心电力/散热",
    "ITRI": "智能电表",
    # Networking / optical / telecom
    "LITE": "光通信器件",
    "NBIS": "AI云基础设施",
    "GLW": "光纤/显示玻璃",
    "VZ": "无线通信运营商",
    "TEL": "连接器/传感器",
    "KN": "声学元件",
    "CHTR": "有线宽带",
    "EXTR": "企业网络设备",
    "CALX": "宽带接入设备",
    "NOK": "通信设备",
    # Internet / ads / content
    "META": "社交广告",
    "NFLX": "流媒体内容",
    "APP": "移动广告平台",
    "RDDT": "社区内容平台",
    "PINS": "视觉社交广告",
    "SRAD": "体育数据/API",
    "LBRDK": "宽带/媒体控股",
    "SIRI": "卫星广播/音频",
    # Software / SaaS
    "NOW": "IT服务管理SaaS",
    "CRM": "CRM企业软件",
    "ADBE": "创意/营销软件",
    "CRWD": "终端安全",
    "PANW": "网络安全平台",
    "NET": "边缘网络/安全",
    "DDOG": "云监控",
    "MDB": "文档数据库",
    "SEMR": "搜索营销SaaS",
    "PEGA": "流程自动化软件",
    "WK": "财务报表SaaS",
    "FICO": "信用评分/决策软件",
    "FRSH": "客服SaaS",
    # EV / robot / auto
    "TSLA": "电动车/自动驾驶",
    "TM": "全球整车制造",
    "RIVN": "电动皮卡/SUV",
    "LCID": "豪华电动车",
    "XPEV": "智能电动车",
    "NIO": "智能电动车",
    "VC": "汽车座舱电子",
    "OUST": "激光雷达",
    "MBLY": "ADAS视觉芯片",
    "GNTX": "汽车电子/后视镜",
    "CTOS": "商用车租赁",
    # Finance / crypto
    "COIN": "加密交易所",
    "MSTR": "比特币持仓公司",
    "HOOD": "零售券商/交易App",
    "SOFI": "消费金融平台",
    "PYPL": "数字支付",
    "SQ": "商户支付/金融App",
    "IBKR": "电子券商",
    "JPM": "大型银行",
    "GS": "投行/资管",
    "V": "卡组织支付网络",
    "BRK-B": "保险+控股集团",
    "CASH": "社区银行",
    "SLM": "学生贷款",
    "EVO": "支付处理/收单",
    "LC": "在线消费贷",
    "TCBI": "区域银行",
    "ENVA": "在线小额信贷",
    "HLNE": "另类资产管理",
    "CNOB": "区域银行",
    "EBC": "区域银行",
    "WEX": "车队/企业支付",
    # Health care
    "LLY": "GLP-1/创新药",
    "NVO": "GLP-1/糖尿病药",
    "UNH": "医保管理",
    "MRK": "大型制药",
    "VRTX": "罕见病药",
    "OGN": "女性健康/仿制药",
    "CAH": "药品分销",
    "DHR": "生命科学工具",
    "ABCL": "抗体发现平台",
    "NTLA": "基因编辑疗法",
    "ACLX": "肿瘤细胞疗法",
    "ERAS": "肿瘤靶向药",
    "UHS": "医院运营",
    "INBX": "肿瘤免疫药",
    "MOH": "医保管理",
    "WST": "药物包装/给药系统",
    "ICLR": "临床CRO",
    "GRFS": "血浆制品",
    "TFX": "医疗器械",
    "MEDP": "临床CRO",
    # Industrial / aerospace / defense
    "RKLB": "小型火箭发射",
    "ASTS": "卫星直连手机",
    "BA": "商用飞机/军工",
    "LMT": "军工主承包商",
    "NOC": "军工/航天系统",
    "HII": "军舰制造",
    "DRS": "军用电子",
    "VVX": "国防服务",
    "SON": "工业包装",
    "FLS": "工业泵阀",
    "MOG-B": "精密控制系统",
    "PBI": "邮政/物流设备",
    "AZZ": "金属镀锌/涂层",
    "SANM": "电子制造服务",
    "RDW": "空间基础设施",
    "VTOL": "海上直升机服务",
    "ICFI": "政府咨询",
    "TNC": "清洁设备",
    "CAT": "工程机械",
    "RTX": "航空发动机/军工",
    "PLOW": "扫雪/卡车附件",
    "PATK": "房车/船舶零部件",
    "CAE": "飞行模拟训练",
    "TTEK": "工程咨询",
    "SKYW": "区域航空",
    "WWD": "航空/能源控制系统",
    # Consumer / commerce
    "AAPL": "消费电子生态",
    "PDD": "折扣电商",
    "BABA": "中国电商+云",
    "SHOP": "电商建站平台",
    "MELI": "拉美电商/支付",
    "COST": "会员制仓储零售",
    "WMT": "综合零售",
    "HD": "家装零售",
    "BKNG": "在线旅游",
    "DPZ": "披萨连锁",
    "DLTR": "折扣零售",
    "AZO": "汽配零售",
    "MTN": "滑雪度假村",
    "STRA": "职业教育",
    "EDU": "教育培训",
    "MCRI": "赌场度假村",
    "CAR": "租车服务",
    "PENN": "博彩/体育娱乐",
    "LULU": "运动服饰",
    "BYD": "赌场酒店",
    "CHDN": "赛马/博彩娱乐",
    "LVS": "澳门/新加坡赌场",
    "TSCO": "乡村零售",
    "TAL": "教育培训",
    "ABG": "汽车经销商",
    # Energy / materials
    "SHEL": "综合油气",
    "XOM": "综合油气",
    "CVX": "综合油气",
    "EPD": "油气中游管道",
    "WES": "油气中游管道",
    "CNQ": "油砂/油气生产",
    "KRP": "矿权版税",
    "TDW": "海工船服务",
    "KEN": "电力/能源控股",
    "CLF": "钢铁",
    "FCX": "铜矿",
    "CCJ": "铀矿",
    "ALB": "锂材料",
    "LAC": "锂矿开发",
    "SXT": "色素/香精材料",
    "KALU": "铝材",
    "DQ": "多晶硅",
    "HLX": "海底油服",
    "AAUC": "黄金勘探",
    "LPX": "木建材",
    "RS": "金属分销",
    "ROG": "高性能材料",
    "TX": "钢铁",
    # Real estate / infrastructure
    "WSO": "暖通设备分销",
    "NHI": "医疗地产REIT",
    "URI": "设备租赁",
    "BLD": "建筑保温安装",
    "MAS": "家装建材",
    "MGRC": "模块建筑租赁",
    "ZWS": "水处理/基础设施",
    "IIPR": "大麻地产REIT",
    "SILA": "医疗地产REIT",
    "QXO": "建筑材料分销",
    "FSV": "物业服务",
    "EGP": "工业地产REIT",
}

BUCKET_ROLE_FALLBACKS = {
    "AI算力/云": "AI/云基础设施",
    "半导体链": "芯片/半导体环节",
    "数据中心电力": "数据中心电力设备",
    "通信/网络设备": "网络/通信基础设施",
    "互联网/广告": "互联网平台/广告",
    "软件/SaaS": "企业软件/SaaS",
    "自动驾驶/机器人": "汽车/自动化",
    "金融/加密": "金融服务",
    "医药/生命科学": "医药/生命科学",
    "工业/航天/国防": "工业/航天/国防",
    "消费/电商": "消费/电商服务",
    "能源/材料": "能源/材料",
    "地产/基础设施": "地产/基础设施",
    "ETF/宏观工具": "ETF/指数工具",
    "其他": "待补业务标签",
}

BUSINESS_ROLE_KEYWORDS = [
    (("gpu", "accelerator"), "GPU/AI加速器"),
    (("cloud", "data center"), "云/数据中心"),
    (("semiconductor equipment", "wafer", "lithography", "etch"), "半导体设备"),
    (("semiconductor", "chip", "soc"), "芯片/半导体"),
    (("memory", "dram", "nand", "storage"), "存储芯片/存储"),
    (("optical", "fiber", "laser"), "光通信/光器件"),
    (("communication equipment", "broadband", "telecom"), "通信设备/运营"),
    (("software", "saas"), "企业软件/SaaS"),
    (("cybersecurity", "security"), "网络安全"),
    (("advertising", "internet content", "media", "streaming"), "互联网内容/广告"),
    (("auto", "vehicle", "ev", "lidar"), "汽车/自动驾驶"),
    (("bank", "capital markets", "insurance", "financial"), "金融服务"),
    (("crypto", "bitcoin"), "加密资产相关"),
    (("biotech", "therapeutics", "drug", "pharma"), "创新药/生物技术"),
    (("medical", "diagnostics", "healthcare"), "医疗服务/器械"),
    (("aerospace", "defense"), "航天/军工"),
    (("industrial", "machinery", "engineering"), "工业设备/服务"),
    (("retail", "e-commerce", "consumer"), "零售/消费"),
    (("casino", "gaming", "travel", "restaurant"), "旅游/博彩/餐饮"),
    (("oil", "gas", "energy"), "油气/能源"),
    (("mining", "metal", "chemical", "materials"), "材料/矿业"),
    (("reit", "real estate"), "REIT/地产"),
    (("construction", "infrastructure", "building products"), "建筑/基建"),
]


def _send_group_message(message: str) -> bool:
    """Route a single message to the public group."""
    return send_message(message, channel="group")


def _send_group_report(message: str) -> bool:
    """Send the morning report to the public group, splitting when needed."""
    ok = True
    for part in split_message(message, split_marker="*D. Dollar Volume*"):
        ok = _send_group_message(part) and ok
    return ok


def _send_group_image_report(image_paths: list[Path]) -> bool:
    """Send each visual morning-report section as one Telegram photo."""
    ok = True
    total = len(image_paths)
    for idx, path in enumerate(image_paths, 1):
        caption = "未来资本晨报 {}/{} — {}".format(idx, total, path.stem)
        ok = send_photo(str(path), caption=caption, channel="group") and ok
    return ok


# ============================================================
# 格式化模块
# ============================================================

def _format_market_cap(market_cap: float | None) -> str:
    if not market_cap:
        return "N/A"
    if market_cap >= 1e12:
        return "${:.1f}T".format(market_cap / 1e12)
    if market_cap >= 1e9:
        return "${:.1f}B".format(market_cap / 1e9)
    return "${:.0f}M".format(market_cap / 1e6)


def _clean_company_name(name: str | None) -> str:
    if not name:
        return ""
    cleaned = str(name)
    suffixes = [
        ", Inc.", ", Inc", " Inc.", " Inc", " Corporation", " Corp.", " Corp", " Incorporated",
        " Class A Common Stock", " Common Stock", " plc", " Ltd.", " Ltd",
        " Limited", " N.V.", " S.A.",
    ]
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip()


def _display_company(item: dict, max_len: int = 22) -> str:
    symbol = item.get("symbol", "")
    name = (
        item.get("shortName")
        or item.get("companyName")
        or item.get("longName")
        or item.get("company_name")
        or ""
    )
    name = _clean_company_name(name)
    if not name or name.upper() == symbol.upper():
        return symbol
    if len(name) > max_len:
        name = name[: max_len - 1].rstrip() + "…"
    return "{} {}".format(symbol, name)


def _business_role(item: dict) -> str:
    """Return our own business-role label, never the raw FMP industry string."""
    symbol = (item.get("symbol") or "").upper()
    if symbol in BUSINESS_ROLE_OVERRIDES:
        return BUSINESS_ROLE_OVERRIDES[symbol]

    text = " ".join(
        str(item.get(key) or "")
        for key in [
            "companyName",
            "shortName",
            "longName",
            "company_name",
            "description",
            "industry",
            "sector",
        ]
    ).lower()
    for keywords, label in BUSINESS_ROLE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return label

    bucket = item.get("concept_bucket") or _concept_bucket(item)
    return BUCKET_ROLE_FALLBACKS.get(bucket, "待补业务标签")


def _display_classification(item: dict) -> str:
    return _business_role(item)


def _normalize_metadata_entry(symbol: str, entry: dict) -> dict:
    return {
        "symbol": symbol.upper(),
        "companyName": (
            entry.get("companyName")
            or entry.get("company_name")
            or entry.get("name")
            or entry.get("longName")
            or entry.get("shortName")
            or ""
        ),
        "shortName": entry.get("shortName") or entry.get("companyName") or entry.get("company_name") or "",
        "longName": entry.get("longName") or entry.get("companyName") or entry.get("company_name") or "",
        "sector": entry.get("sector") or "",
        "industry": entry.get("industry") or "",
        "exchange": entry.get("exchange") or entry.get("exchangeShortName") or "",
        "marketCap": entry.get("marketCap") or entry.get("market_cap") or entry.get("mktCap"),
    }


def _merge_metadata_entry(metadata: dict, symbol: str, entry: dict) -> None:
    symbol = symbol.upper()
    normalized = _normalize_metadata_entry(symbol, entry)
    target = metadata.setdefault(symbol, {"symbol": symbol})
    for key, value in normalized.items():
        if value in (None, ""):
            continue
        if key == "marketCap":
            if not target.get(key):
                target[key] = value
        elif not target.get(key) or target.get(key) == symbol:
            target[key] = value


def _iter_profile_records(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        records = []
        for key, value in payload.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("symbol", key)
                records.append(row)
        return records
    return []


def _merge_local_metadata(metadata: dict, symbols: list[str]) -> None:
    """Merge cheap local company metadata from company.db and JSON caches."""
    wanted = {symbol.upper() for symbol in symbols}

    try:
        from terminal.company_store import get_store
        for row in get_store().list_companies():
            symbol = (row.get("symbol") or "").upper()
            if symbol in wanted:
                _merge_metadata_entry(metadata, symbol, row)
    except Exception as exc:
        logger.info("company.db metadata unavailable for morning report: %s", exc)

    for path in [
        DATA_DIR / "pool" / "universe.json",
        DATA_DIR / "fundamental" / "profiles.json",
        SCANS_DIR / "broad_universe.json",
    ]:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.info("metadata cache unreadable %s: %s", path, exc)
            continue
        if isinstance(payload, dict) and isinstance(payload.get("stocks"), dict):
            payload = payload["stocks"]
        for row in _iter_profile_records(payload):
            symbol = (row.get("symbol") or row.get("ticker") or "").upper()
            if symbol in wanted:
                _merge_metadata_entry(metadata, symbol, row)


def _metadata_has_company_classification(entry: dict) -> bool:
    name = (
        entry.get("companyName")
        or entry.get("shortName")
        or entry.get("longName")
        or ""
    )
    has_name = bool(name and name != entry.get("symbol"))
    return has_name and bool(entry.get("sector") or entry.get("industry"))


def _hydrate_signal_metadata(metadata: dict, symbols: list[str]) -> None:
    """Ensure triggered symbols have company names and classification if possible."""
    _merge_local_metadata(metadata, symbols)
    missing = [
        symbol for symbol in sorted({s.upper() for s in symbols})
        if not _metadata_has_company_classification(metadata.get(symbol, {}))
    ]
    if not missing:
        return

    try:
        from config.settings import FMP_API_KEY
        if not FMP_API_KEY:
            return
        from src.data.fmp_client import FMPClient
        client = FMPClient()
        for symbol in missing:
            profile = client.get_profile(symbol)
            if profile:
                _merge_metadata_entry(metadata, symbol, profile)
    except Exception as exc:
        logger.info("live metadata fallback skipped: %s", exc)


def _theme_bucket_for_symbol(symbol: str) -> str | None:
    try:
        from config.settings import THEME_KEYWORDS_SEED
        for theme, bucket in THEME_BUCKET_HINTS.items():
            tickers = THEME_KEYWORDS_SEED.get(theme, {}).get("tickers", [])
            if symbol in {ticker.upper() for ticker in tickers}:
                return bucket
    except Exception:
        return None
    return None


def _concept_bucket(item: dict) -> str:
    symbol = (item.get("symbol") or "").upper()
    if "-P" in symbol:
        return "金融/加密"
    if symbol in ETF_SYMBOLS:
        return "ETF/宏观工具"
    if symbol in SYMBOL_BUCKET_OVERRIDES:
        return SYMBOL_BUCKET_OVERRIDES[symbol]

    theme_bucket = _theme_bucket_for_symbol(symbol)
    if theme_bucket:
        return theme_bucket

    text = " ".join(
        str(item.get(key) or "")
        for key in ["companyName", "shortName", "longName", "sector", "industry"]
    ).lower()

    if any(k in text for k in ["semiconductor", "chip", "foundry", "memory", "dram", "nand", "computer hardware", "storage"]):
        return "半导体链"
    if any(k in text for k in ["data center", "cloud", "gpu", "artificial intelligence", "generative ai", "quantum"]):
        return "AI算力/云"
    if any(k in text for k in ["electrical", "electric", "power", "nuclear", "utility", "utilities", "grid", "fuel cell"]):
        return "数据中心电力"
    if any(k in text for k in ["communication equipment", "communication services", "network", "optical", "telecom", "satellite", "broadband", "cable"]):
        return "通信/网络设备"
    if any(k in text for k in ["internet content", "advertising", "media", "streaming", "entertainment"]):
        return "互联网/广告"
    if any(k in text for k in ["software", "saas", "cybersecurity", "information technology services"]):
        return "软件/SaaS"
    if any(k in text for k in ["auto", "vehicle", "electric vehicle", "robot", "lidar", "battery"]):
        return "自动驾驶/机器人"
    if any(k in text for k in ["financial", "bank", "capital markets", "crypto", "bitcoin", "insurance", "fintech"]):
        return "金融/加密"
    if any(k in text for k in ["health", "biotech", "drug", "pharma", "medical", "therapeutics"]):
        return "医药/生命科学"
    if any(k in text for k in ["aerospace", "defense", "industrial", "machinery", "logistics", "engineering"]):
        return "工业/航天/国防"
    if any(k in text for k in ["consumer", "retail", "e-commerce", "apparel", "restaurant", "travel", "casino", "gaming", "education"]):
        return "消费/电商"
    if any(k in text for k in ["energy", "materials", "mining", "chemical", "metal", "lithium", "oil", "gas"]):
        return "能源/材料"
    if any(k in text for k in ["real estate", "reit", "construction", "infrastructure", "building products"]):
        return "地产/基础设施"
    if any(k in text for k in ["etf", "fund", "trust"]):
        return "ETF/宏观工具"
    return "其他"


def _layer_for_symbol(symbol: str, metadata: dict, pool_symbols: set) -> str:
    if symbol in pool_symbols:
        return "pool"
    market_cap = metadata.get(symbol, {}).get("marketCap") or 0
    if market_cap >= EXTENDED_LAYER_MIN_MCAP:
        return "extend"
    return "broad"


def _frame_with_date(symbol: str, frame) -> object:
    df = frame.reset_index().copy()
    if "date" not in df.columns:
        first = df.columns[0]
        df = df.rename(columns={first: "date"})
    df["symbol"] = symbol
    return df


def _group_by_layer(items: list) -> dict:
    grouped = {layer: [] for layer in LAYER_ORDER}
    for item in items:
        grouped.setdefault(item.get("layer", "broad"), []).append(item)
    return grouped


def _format_layered_items(
    items: list,
    empty_text: str,
    formatter,
    limit_per_layer: int = LAYER_TOP_N,
) -> list[str]:
    if not items:
        return [empty_text]

    lines = []
    grouped = _group_by_layer(items)
    for layer in LAYER_ORDER:
        layer_items = grouped.get(layer, [])
        lines.append("{}:".format(LAYER_LABELS[layer]))
        if not layer_items:
            lines.append("  无")
            continue
        for item in layer_items[:limit_per_layer]:
            lines.append("  " + formatter(item))
        if len(layer_items) > limit_per_layer:
            lines.append("  ... +{} more".format(len(layer_items) - limit_per_layer))
    return lines


def _group_by_concept_bucket(items: list) -> dict:
    grouped = {bucket: [] for bucket in CONCEPT_BUCKET_ORDER}
    for item in items:
        bucket = item.get("concept_bucket") or _concept_bucket(item)
        grouped.setdefault(bucket, []).append(item)
    return {bucket: grouped[bucket] for bucket in CONCEPT_BUCKET_ORDER if grouped.get(bucket)}


def _format_bucketed_items(items: list, empty_text: str, formatter) -> list[str]:
    if not items:
        return [empty_text]

    lines = []
    grouped = _group_by_concept_bucket(items)
    for bucket, bucket_items in grouped.items():
        lines.append("{} ({}):".format(bucket, len(bucket_items)))
        for item in bucket_items:
            lines.append("  " + formatter(item))
    return lines


def _format_bucketed_table(
    items: list,
    empty_text: str,
    header: str,
    formatter,
) -> list[str]:
    if not items:
        return [empty_text]

    lines = [header]
    grouped = _group_by_concept_bucket(items)
    for bucket, bucket_items in grouped.items():
        lines.append("{} ({}):".format(bucket, len(bucket_items)))
        for item in bucket_items:
            lines.append("  " + formatter(item))
    return lines


def _compact_company(item: dict) -> str:
    symbol = item.get("symbol", "")
    name = _display_company(item, max_len=18)
    if name == symbol or name.startswith(symbol + " "):
        return name
    return symbol


def _enrich_with_layer(item: dict, metadata: dict, pool_symbols: set) -> dict:
    symbol = item["symbol"]
    meta = metadata.get(symbol, {})
    enriched = dict(item)
    for key in ["companyName", "shortName", "longName", "sector", "industry", "exchange"]:
        if meta.get(key):
            enriched[key] = meta[key]
    enriched["marketCap"] = meta.get("marketCap")
    enriched["layer"] = _layer_for_symbol(symbol, metadata, pool_symbols)
    enriched["concept_bucket"] = _concept_bucket(enriched)
    return enriched


def build_market_signal_report(symbols_override: list[str] | None = None) -> dict:
    """Build broad-universe technical signal payload for the merged morning report."""
    from datetime import date

    from scripts.broad_market_scan import (
        BROAD_SCAN_RETURN_THRESHOLD,
        BROAD_SCAN_RVOL_THRESHOLD,
        fetch_universe_metadata,
        load_price_frames,
        scan_candidates,
    )
    from src.data.market_store import get_store
    from src.indicators.dv_acceleration import scan_dv_acceleration
    from src.indicators.pmarp import analyze_pmarp
    from src.indicators.rvol_sustained import scan_rvol_sustained

    pool_symbols = set(get_symbols())
    if symbols_override:
        symbols = sorted({s.strip().upper() for s in symbols_override if s.strip()})
        store = get_store()
        bulk_caps = store.get_bulk_market_caps_at(date.today().isoformat())
        metadata = {
            symbol: {
                "marketCap": bulk_caps.get(symbol),
                "shortName": symbol,
                "longName": symbol,
                "exchange": "DB",
            }
            for symbol in symbols
        }
    else:
        universe_cache = fetch_universe_metadata(as_of_date=date.today().isoformat(), min_mcap_b=1.0)
        metadata = universe_cache.get("stocks", {})
        symbols = sorted(metadata.keys())

    _merge_local_metadata(metadata, symbols)

    price_frames = load_price_frames(symbols, rows_needed=MORNING_SIGNAL_PRICE_ROWS)
    price_dict = {
        symbol: _frame_with_date(symbol, frame)
        for symbol, frame in price_frames.items()
    }

    broad_scan = scan_candidates(price_frames, metadata, pool_symbols)

    db_rows = [
        {
            "symbol": item["symbol"],
            "date": broad_scan["scan_date"],
            "rvol": item["rvol"],
            "return_pct": item["return_pct"],
            "market_cap": item.get("marketCap"),
            "in_pool": item.get("in_pool", False),
        }
        for item in broad_scan["all_triggered"]
    ]
    if db_rows:
        get_store().save_broad_scan_hits(db_rows)

    pmarp_raw = []
    for symbol, frame in price_dict.items():
        result = analyze_pmarp(frame)
        if result.get("signal") == "oversold_recovery":
            pmarp_raw.append({
                "symbol": symbol,
                "value": result.get("current"),
                "previous": result.get("previous"),
                "signal": result.get("signal"),
            })

    dv_df = scan_dv_acceleration(price_dict, threshold=DV_ACCELERATION_THRESHOLD)
    dv_raw = []
    if len(dv_df) > 0:
        for row in dv_df[dv_df["signal"]].to_dict("records"):
            dv_raw.append(row)

    rvol_raw = scan_rvol_sustained(price_dict, threshold=RVOL_SUSTAINED_THRESHOLD)

    signal_symbols = [
        item["symbol"]
        for item in (
            list(broad_scan["all_triggered"])
            + pmarp_raw
            + dv_raw
            + rvol_raw
        )
    ]
    _hydrate_signal_metadata(metadata, signal_symbols)

    broad_hits = sorted(
        [_enrich_with_layer(item, metadata, pool_symbols) for item in broad_scan["all_triggered"]],
        key=lambda x: (-x["rvol"], -x["return_pct"], x["symbol"]),
    )

    pmarp_signals = [
        _enrich_with_layer(item, metadata, pool_symbols)
        for item in pmarp_raw
    ]
    pmarp_signals.sort(key=lambda x: (x.get("value") or 0, x["symbol"]))

    dv_hits = [
        _enrich_with_layer(item, metadata, pool_symbols)
        for item in dv_raw
    ]
    dv_hits.sort(key=lambda x: (-(x.get("ratio") or 0), x["symbol"]))

    rvol_hits = [
        _enrich_with_layer(item, metadata, pool_symbols)
        for item in rvol_raw
    ]

    scan_dates = [frame.index.max() for frame in price_frames.values() if not frame.empty]
    as_of = max(scan_dates).date().isoformat() if scan_dates else date.today().isoformat()

    return {
        "as_of": as_of,
        "symbols_scanned": len(symbols),
        "symbols_with_data": len(price_frames),
        "layer_counts": {
            layer: sum(
                1 for symbol in symbols
                if _layer_for_symbol(symbol, metadata, pool_symbols) == layer
            )
            for layer in LAYER_ORDER
        },
        "broad_scan": {
            "criteria": "RVOL ≥{:.0f}σ + 涨 ≥{:.0f}%".format(
                BROAD_SCAN_RVOL_THRESHOLD,
                BROAD_SCAN_RETURN_THRESHOLD,
            ),
            "hits": broad_hits,
            "triggered_total": broad_scan["triggered_total"],
        },
        "pmarp": {
            "criteria": "PMARP 上穿 2%",
            "hits": pmarp_signals,
        },
        "dv_acceleration": {
            "criteria": "DV >{:.1f}x".format(DV_ACCELERATION_THRESHOLD),
            "hits": dv_hits,
        },
        "rvol_sustained": {
            "criteria": "RVOL >{:.1f}σ 持续".format(RVOL_SUSTAINED_THRESHOLD),
            "hits": rvol_hits,
        },
    }


def format_section_broad_signal(market_signals: dict) -> str:
    section = market_signals.get("broad_scan", {})
    lines = ["*1. 广扫标准 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无广扫触发",
        "标的 | 业务角色 | RVOL | 涨幅 | 市值",
        lambda item: "{} | {} | {:.1f}σ | {:+.1f}% | {}".format(
            _compact_company(item),
            _display_classification(item),
            item["rvol"],
            item["return_pct"],
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def format_section_layered_pmarp(market_signals: dict) -> str:
    section = market_signals.get("pmarp", {})
    lines = ["*2. PMARP 信号 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无 PMARP 信号",
        "标的 | 业务角色 | 当前 | 变化 | 市值",
        lambda item: "{} | {} | {:.1f}% | {:.1f}→{:.1f} | {}".format(
            _compact_company(item),
            _display_classification(item),
            item.get("value") or 0,
            item.get("previous") or 0,
            item.get("value") or 0,
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def format_section_layered_dv(market_signals: dict) -> str:
    section = market_signals.get("dv_acceleration", {})
    lines = ["*3. 量能加速 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无加速信号",
        "标的 | 业务角色 | 倍数 | 5d/20d | 市值",
        lambda item: "{} | {} | {:.1f}x | {}/{} | {}".format(
            _compact_company(item),
            _display_classification(item),
            item.get("ratio") or 0,
            format_dv(item.get("dv_5d") or 0),
            format_dv(item.get("dv_20d") or 0),
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def format_section_layered_rvol(market_signals: dict) -> str:
    section = market_signals.get("rvol_sustained", {})
    level_labels = {
        "sustained_5d": "5日连续",
        "sustained_3d": "3日连续",
        "single": "单日",
    }
    lines = ["*4. RVOL 持续放量 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无持续放量信号",
        "标的 | 业务角色 | 形态 | 最新 | 市值",
        lambda item: "{} | {} | {} | {:.1f}σ | {}".format(
            _compact_company(item),
            _display_classification(item),
            level_labels.get(item.get("level"), item.get("level", "")),
            item.get("latest_rvol") or 0,
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)

def format_section_a(indicator_summary: dict) -> str:
    """A. PMARP 极值 (仅保留上穿2%报警)"""
    lines = ["*A. PMARP 极值*"]

    crossovers = indicator_summary.get("pmarp_crossovers", {})

    # 只保留上穿2%报警。
    # 98% 上下穿已移除；下穿2%也不再作为晨报报警信号。
    recovery = crossovers.get("recovery_2", [])

    if recovery:
        items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in recovery)
        lines.append("上穿2%: {}".format(items))
    else:
        lines.append("今日无极值信号")

    return "\n".join(lines)


def format_section_b(dv_df) -> str:
    """B. 量能加速"""
    lines = ["*C. 量能加速 (DV>{:.1f}x)*".format(DV_ACCELERATION_THRESHOLD)]

    fired = dv_df[dv_df["signal"]] if len(dv_df) > 0 else dv_df
    if len(fired) == 0:
        lines.append("无加速信号")
    else:
        for _, row in fired.head(10).iterrows():
            lines.append("{}: 5d={}/20d={} = {:.1f}x".format(
                row["symbol"],
                format_dv(row["dv_5d"]),
                format_dv(row["dv_20d"]),
                row["ratio"]))

    return "\n".join(lines)


def format_section_c(rvol_list: list) -> str:
    """C. RVOL 持续放量"""
    lines = ["*C. RVOL 持续放量*"]

    level_icons = {
        "sustained_5d": "5日连续:",
        "sustained_3d": "3日连续:",
        "single": "单日>2s:",
    }

    if not rvol_list:
        lines.append("无持续放量信号")
    else:
        for item in rvol_list[:15]:
            icon = level_icons.get(item["level"], "")
            vals = " ".join("{:.1f}s".format(v) for v in item["values"][:5])
            lines.append("{} {} ({})".format(icon, item["symbol"], vals))

    return "\n".join(lines)


def _normalize_dv_items(dv_result: dict) -> dict:
    """Normalize Dollar Volume rows into the same enriched item shape as signals."""
    rankings = dv_result.get("rankings", [])
    new_faces = dv_result.get("new_faces", [])
    metadata = {}
    symbols = []
    for row in rankings + new_faces:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            continue
        symbols.append(symbol)
        metadata[symbol] = dict(row)
    if symbols:
        _merge_local_metadata(metadata, symbols)

    try:
        pool_symbols = set(get_symbols())
    except Exception:
        pool_symbols = set()

    def normalize(row: dict) -> dict:
        symbol = (row.get("symbol") or "").upper()
        item = dict(metadata.get(symbol) or {})
        item.update({k: v for k, v in row.items() if v not in (None, "")})
        item["symbol"] = symbol or item.get("symbol", "")
        if row.get("company_name") and not item.get("companyName"):
            item["companyName"] = row.get("company_name")
        if row.get("market_cap") and not item.get("marketCap"):
            item["marketCap"] = row.get("market_cap")
        item.setdefault("concept_bucket", _concept_bucket(item))
        layer_meta = {symbol: {"marketCap": item.get("marketCap") or 0}}
        item["layer"] = _layer_for_symbol(symbol, layer_meta, pool_symbols)
        return item

    return {
        "rankings": [normalize(row) for row in rankings],
        "new_faces": [normalize(row) for row in new_faces],
    }


def format_section_d(dv_result: dict) -> str:
    """D. Dollar Volume"""
    lines = ["*D. Dollar Volume*"]
    normalized = _normalize_dv_items(dv_result)

    # 新面孔
    if normalized["new_faces"]:
        lines.append("新面孔:")
        lines.extend(_format_bucketed_table(
            normalized["new_faces"],
            "无新面孔",
            "标的 | 业务角色 | 排名 | 成交额",
            lambda item: "{} | {} | #{} | {}".format(
                _compact_company(item),
                _display_classification(item),
                item["rank"],
                format_dv(item["dollar_volume"]),
            ),
        ))

    # Full ranking payload. Telegram splitting handles long reports.
    if normalized["rankings"]:
        lines.append("成交额 Top {}:".format(len(normalized["rankings"])))
        lines.extend(_format_bucketed_table(
            normalized["rankings"],
            "无成交额排行",
            "标的 | 业务角色 | 排名 | 成交额 | 价格",
            lambda item: "{} | {} | #{} | {} | ${:.0f}".format(
                _compact_company(item),
                _display_classification(item),
                item["rank"],
                format_dv(item["dollar_volume"]),
                item["price"],
            ),
        ))

    return "\n".join(lines)


def _visual_row(item: dict, cells: list[str]) -> dict:
    return {
        "layer": item.get("layer", "broad"),
        "bucket": item.get("concept_bucket") or _concept_bucket(item),
        "cells": [str(cell) for cell in cells],
    }


def _visual_company(item: dict) -> str:
    return _display_company(item, max_len=30)


def _build_visual_block(
    title: str,
    columns: list[str],
    items: list[dict],
    row_builder,
    widths: list[int],
) -> dict:
    return {
        "title": title,
        "columns": columns,
        "widths": widths,
        "rows": [_visual_row(item, row_builder(item)) for item in items],
    }


def build_morning_visual_sections(
    market_signals: dict | None = None,
    dv_result: dict | None = None,
) -> list[dict]:
    """Build image-report section specs grouped by layer and concept bucket."""
    sections = []
    as_of = (market_signals or {}).get("as_of") or datetime.now().strftime("%Y-%m-%d")
    common_subtitle = "信号日 {} | Pool / Extend / Broad 分层，层内按题材聚类".format(as_of)

    if market_signals:
        broad = market_signals.get("broad_scan", {})
        sections.append({
            "slug": "01_broad_signal",
            "title": "1. 广扫标准",
            "subtitle": "{} | {}".format(broad.get("criteria", ""), common_subtitle),
            "blocks": [
                _build_visual_block(
                    "触发公司",
                    ["标的", "业务角色", "RVOL", "涨幅", "市值"],
                    broad.get("hits", []),
                    lambda item: [
                        _visual_company(item),
                        _display_classification(item),
                        "{:.1f}σ".format(item.get("rvol") or 0),
                        "{:+.1f}%".format(item.get("return_pct") or 0),
                        _format_market_cap(item.get("marketCap")),
                    ],
                    [380, 430, 150, 150, 160],
                ),
            ],
        })

        pmarp = market_signals.get("pmarp", {})
        sections.append({
            "slug": "02_pmarp",
            "title": "2. PMARP 信号",
            "subtitle": "{} | {}".format(pmarp.get("criteria", ""), common_subtitle),
            "blocks": [
                _build_visual_block(
                    "上穿/修复",
                    ["标的", "业务角色", "当前", "变化", "市值"],
                    pmarp.get("hits", []),
                    lambda item: [
                        _visual_company(item),
                        _display_classification(item),
                        "{:.1f}%".format(item.get("value") or 0),
                        "{:.1f}→{:.1f}".format(item.get("previous") or 0, item.get("value") or 0),
                        _format_market_cap(item.get("marketCap")),
                    ],
                    [380, 430, 150, 190, 160],
                ),
            ],
        })

        dv_acc = market_signals.get("dv_acceleration", {})
        sections.append({
            "slug": "03_dv_acceleration",
            "title": "3. 量能加速",
            "subtitle": "{} | {}".format(dv_acc.get("criteria", ""), common_subtitle),
            "blocks": [
                _build_visual_block(
                    "DV 加速",
                    ["标的", "业务角色", "倍数", "5d/20d", "市值"],
                    dv_acc.get("hits", []),
                    lambda item: [
                        _visual_company(item),
                        _display_classification(item),
                        "{:.1f}x".format(item.get("ratio") or 0),
                        "{}/{}".format(
                            format_dv(item.get("dv_5d") or 0),
                            format_dv(item.get("dv_20d") or 0),
                        ),
                        _format_market_cap(item.get("marketCap")),
                    ],
                    [380, 430, 150, 240, 160],
                ),
            ],
        })

        rvol = market_signals.get("rvol_sustained", {})
        level_labels = {
            "sustained_5d": "5日连续",
            "sustained_3d": "3日连续",
            "single": "单日",
        }
        sections.append({
            "slug": "04_rvol_sustained",
            "title": "4. RVOL 持续放量",
            "subtitle": "{} | {}".format(rvol.get("criteria", ""), common_subtitle),
            "blocks": [
                _build_visual_block(
                    "持续放量",
                    ["标的", "业务角色", "形态", "最新", "市值"],
                    rvol.get("hits", []),
                    lambda item: [
                        _visual_company(item),
                        _display_classification(item),
                        level_labels.get(item.get("level"), item.get("level", "")),
                        "{:.1f}σ".format(item.get("latest_rvol") or 0),
                        _format_market_cap(item.get("marketCap")),
                    ],
                    [380, 430, 170, 150, 160],
                ),
            ],
        })

    if dv_result:
        normalized = _normalize_dv_items(dv_result)
        blocks = []
        if normalized["new_faces"]:
            blocks.append(_build_visual_block(
                "新面孔",
                ["标的", "业务角色", "排名", "成交额"],
                normalized["new_faces"],
                lambda item: [
                    _visual_company(item),
                    _display_classification(item),
                    "#{}".format(item.get("rank", "")),
                    format_dv(item.get("dollar_volume") or 0),
                ],
                [420, 470, 160, 230],
            ))
        if normalized["rankings"]:
            blocks.append(_build_visual_block(
                "成交额 Top {}".format(len(normalized["rankings"])),
                ["标的", "业务角色", "排名", "成交额", "价格"],
                normalized["rankings"],
                lambda item: [
                    _visual_company(item),
                    _display_classification(item),
                    "#{}".format(item.get("rank", "")),
                    format_dv(item.get("dollar_volume") or 0),
                    "${:.0f}".format(item.get("price") or 0),
                ],
                [380, 430, 150, 230, 150],
            ))
        if blocks:
            sections.append({
                "slug": "05_dollar_volume",
                "title": "D. Dollar Volume",
                "subtitle": common_subtitle,
                "blocks": blocks,
            })

    return sections


_VISUAL_FONT_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "bold": [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}

_VISUAL_LAYER_COLORS = {
    "pool": ("#1d4ed8", "#dbeafe"),
    "extend": ("#b45309", "#fef3c7"),
    "broad": ("#334155", "#e2e8f0"),
}
_TELEGRAM_PHOTO_MAX_DIMENSION_SUM = 9800


def _load_visual_font(size: int, bold: bool = False):
    from PIL import ImageFont

    key = "bold" if bold else "regular"
    for candidate in _VISUAL_FONT_CANDIDATES[key]:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text: str, font, max_width: int) -> str:
    text = str(text)
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ellipsis


def _draw_fit(draw, xy: tuple[int, int], text: str, font, fill: str, max_width: int) -> None:
    draw.text(xy, _fit_text(draw, text, font, max_width), font=font, fill=fill)


def _rows_by_layer_and_bucket(rows: list[dict]) -> dict:
    grouped = {
        layer: {bucket: [] for bucket in CONCEPT_BUCKET_ORDER}
        for layer in LAYER_ORDER
    }
    for row in rows:
        layer = row.get("layer", "broad")
        bucket = row.get("bucket") or "其他"
        grouped.setdefault(layer, {}).setdefault(bucket, []).append(row)
    return grouped


def _estimate_visual_height(section: dict) -> int:
    height = 190
    for block in section.get("blocks", []):
        height += 70
        grouped = _rows_by_layer_and_bucket(block.get("rows", []))
        for layer in LAYER_ORDER:
            layer_rows = sum(len(rows) for rows in grouped.get(layer, {}).values())
            height += 54
            if not layer_rows:
                height += 42
                continue
            for bucket in CONCEPT_BUCKET_ORDER:
                rows = grouped.get(layer, {}).get(bucket, [])
                if rows:
                    height += 38 + 40 + 44 * len(rows)
    return max(height + 260, 640)


def _scaled_widths(widths: list[int], total_width: int) -> list[int]:
    raw_total = sum(widths) or total_width
    scaled = [max(80, int(w * total_width / raw_total)) for w in widths]
    diff = total_width - sum(scaled)
    if scaled:
        scaled[-1] += diff
    return scaled


def _draw_visual_table_header(draw, x: int, y: int, col_widths: list[int], columns: list[str], font) -> int:
    row_h = 40
    draw.rectangle([x, y, x + sum(col_widths), y + row_h], fill="#f1f5f9")
    cur_x = x
    for width, column in zip(col_widths, columns):
        _draw_fit(draw, (cur_x + 14, y + 9), column, font, "#334155", width - 24)
        cur_x += width
    return y + row_h


def _resize_for_telegram_photo(image):
    width, height = image.size
    if width + height <= _TELEGRAM_PHOTO_MAX_DIMENSION_SUM:
        return image

    scale = _TELEGRAM_PHOTO_MAX_DIMENSION_SUM / float(width + height)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    try:
        from PIL import Image
        resampling = Image.Resampling.LANCZOS
    except Exception:
        resampling = 1
    return image.resize(new_size, resampling)


def render_morning_report_images(
    market_signals: dict | None = None,
    dv_result: dict | None = None,
    output_dir: str | Path | None = None,
) -> list[Path]:
    """Render each morning-report section as one PNG image."""
    from PIL import Image, ImageDraw

    sections = build_morning_visual_sections(market_signals=market_signals, dv_result=dv_result)
    if not sections:
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else SCANS_DIR / "morning_images_{}".format(timestamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    width = 1800
    margin = 58
    content_w = width - margin * 2
    title_font = _load_visual_font(44, bold=True)
    subtitle_font = _load_visual_font(24)
    block_font = _load_visual_font(28, bold=True)
    layer_font = _load_visual_font(25, bold=True)
    bucket_font = _load_visual_font(23, bold=True)
    header_font = _load_visual_font(21, bold=True)
    row_font = _load_visual_font(22)
    small_font = _load_visual_font(20)

    image_paths = []
    for index, section in enumerate(sections, 1):
        height = _estimate_visual_height(section) + 2000
        image = Image.new("RGB", (width, height), "#f8fafc")
        draw = ImageDraw.Draw(image)

        y = 46
        draw.rounded_rectangle([margin, y, width - margin, y + 92], radius=18, fill="#0f172a")
        _draw_fit(draw, (margin + 34, y + 22), section["title"], title_font, "#ffffff", content_w - 68)
        y += 108
        _draw_fit(draw, (margin + 4, y), section.get("subtitle", ""), subtitle_font, "#475569", content_w)
        y += 54

        for block in section.get("blocks", []):
            draw.text((margin, y), block["title"], font=block_font, fill="#111827")
            y += 46
            grouped = _rows_by_layer_and_bucket(block.get("rows", []))
            col_widths = _scaled_widths(block.get("widths", []), content_w)

            for layer in LAYER_ORDER:
                layer_rows = sum(len(rows) for rows in grouped.get(layer, {}).values())
                dark, light = _VISUAL_LAYER_COLORS[layer]
                draw.rounded_rectangle([margin, y, width - margin, y + 38], radius=10, fill=light)
                layer_label = "{}  {}家公司".format(LAYER_LABELS[layer], layer_rows)
                draw.text((margin + 16, y + 6), layer_label, font=layer_font, fill=dark)
                y += 48

                if not layer_rows:
                    draw.text((margin + 18, y), "无触发", font=small_font, fill="#64748b")
                    y += 42
                    continue

                for bucket in CONCEPT_BUCKET_ORDER:
                    rows = grouped.get(layer, {}).get(bucket, [])
                    if not rows:
                        continue
                    draw.text(
                        (margin + 14, y),
                        "{} ({})".format(bucket, len(rows)),
                        font=bucket_font,
                        fill="#0f172a",
                    )
                    y += 36
                    y = _draw_visual_table_header(draw, margin, y, col_widths, block["columns"], header_font)
                    for row_idx, row in enumerate(rows):
                        row_h = 44
                        fill = "#ffffff" if row_idx % 2 == 0 else "#f8fafc"
                        draw.rectangle([margin, y, width - margin, y + row_h], fill=fill)
                        cur_x = margin
                        for col_width, cell in zip(col_widths, row["cells"]):
                            _draw_fit(draw, (cur_x + 14, y + 10), cell, row_font, "#111827", col_width - 24)
                            cur_x += col_width
                        y += row_h
                    y += 18
                y += 8
            y += 14

        footer_y = y + 18
        draw.text(
            (margin, footer_y),
            "Generated {} | Future Capital Morning Report".format(datetime.now().strftime("%Y-%m-%d %H:%M")),
            font=small_font,
            fill="#64748b",
        )
        final_height = min(height, footer_y + 58)
        image = image.crop((0, 0, width, final_height))
        path = out_dir / "{:02d}_{}.png".format(index, section["slug"])
        image = _resize_for_telegram_photo(image)
        image.save(path, "PNG", optimize=True)
        image_paths.append(path)

    return image_paths



def format_section_market_pulse(market_data: dict) -> str:
    """E. 市场情绪脉搏 (Adanos market-level sentiment)"""
    # Show data date if not today
    dates = set(r.get("date") for r in market_data.values() if isinstance(r, dict) and r.get("date"))
    date_tag = ""
    if dates:
        from datetime import datetime as _dt, timezone as _tz
        _today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        stale = [d for d in dates if d != _today]
        if stale:
            date_tag = " [{}]".format(max(dates))
    lines = ["*E. 市场情绪脉搏{}*".format(date_tag)]

    reddit = market_data.get("reddit")
    x_data = market_data.get("x")

    if not reddit and not x_data:
        lines.append("无市场情绪数据")
        return "\n".join(lines)

    for source, label in [("reddit", "Reddit"), ("x", "𝕏")]:
        row = market_data.get(source)
        if not row:
            continue
        buzz = row.get("buzz_score", 0) or 0
        trend = row.get("trend", "—")
        bull = row.get("bullish_pct", 0) or 0
        bear = row.get("bearish_pct", 0) or 0
        mentions = row.get("mentions", 0) or 0
        sentiment = row.get("sentiment_score")
        sent_str = "{:+.2f}".format(sentiment) if sentiment is not None else "n/a"
        # Trend arrow
        arrow = {"bullish": "↑", "bearish": "↓", "neutral": "→"}.get(trend, "·")
        lines.append("{} {} buzz={:.0f} {}bull/{}bear sent={} ({}提及)".format(
            label, arrow, buzz, bull, bear, sent_str, mentions))

    return "\n".join(lines)


def format_section_trending(trending_data: dict) -> str:
    """F. 社交热门 + 热门板块 (Adanos trending)"""
    data_date = trending_data.get("date", "")
    date_tag = ""
    if data_date:
        from datetime import datetime as _dt, timezone as _tz
        _today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        if data_date != _today:
            date_tag = " [{}]".format(data_date)
    lines = ["*F. 社交热门{}*".format(date_tag)]

    # Sub-section 1: Trending stocks (merge Reddit + X, dedupe by ticker, rank by buzz)
    stocks = trending_data.get("stocks", [])
    if stocks:
        # Merge across sources: keep highest buzz per ticker
        merged = {}
        for row in stocks:
            ticker = row.get("ticker", "")
            if not ticker:
                continue
            buzz = row.get("buzz_score", 0) or 0
            existing = merged.get(ticker)
            if existing is None or buzz > (existing.get("buzz_score", 0) or 0):
                merged[ticker] = row
        ranked = sorted(merged.values(), key=lambda x: x.get("buzz_score", 0) or 0, reverse=True)[10:20]
        lines.append("热门个股 #11-20:")
        for i, row in enumerate(ranked, 11):
            ticker = row.get("ticker", "?")
            buzz = row.get("buzz_score", 0) or 0
            trend = row.get("trend", "")
            sentiment = row.get("sentiment_score")
            sent_str = "{:+.2f}".format(sentiment) if sentiment is not None else ""
            arrow = {"bullish": "↑", "bearish": "↓", "neutral": "→"}.get(trend, "")
            lines.append("  {:>2}. {:<6} buzz={:>5.0f} {} {}".format(
                i, ticker, buzz, arrow, sent_str).rstrip())
    else:
        lines.append("热门个股: 无数据")

    # Sub-section 2: Trending sectors
    sectors = trending_data.get("sectors", [])
    if sectors:
        # Merge across sources: keep highest buzz per sector
        merged_s = {}
        for row in sectors:
            sector = row.get("sector", "")
            if not sector:
                continue
            buzz = row.get("buzz_score", 0) or 0
            existing = merged_s.get(sector)
            if existing is None or buzz > (existing.get("buzz_score", 0) or 0):
                merged_s[sector] = row
        ranked_s = sorted(merged_s.values(), key=lambda x: x.get("buzz_score", 0) or 0, reverse=True)[:8]
        lines.append("")
        lines.append("热门板块:")
        for row in ranked_s:
            sector = row.get("sector", "?")
            buzz = row.get("buzz_score", 0) or 0
            top_tickers = row.get("top_tickers", "")
            if isinstance(top_tickers, list):
                top_tickers = ", ".join(top_tickers[:4])
            elif isinstance(top_tickers, str) and top_tickers.startswith("["):
                try:
                    top_tickers = ", ".join(json.loads(top_tickers)[:4])
                except Exception:
                    pass
            lines.append("  {}: buzz={:.0f} ({})".format(sector, buzz, top_tickers or "—"))

    return "\n".join(lines)


def format_section_social(social_scan: dict) -> str:
    """G. 社交情绪雷达"""
    lines = ["*G. 社交情绪雷达*"]

    alerts = social_scan.get("alerts", [])
    all_signals = social_scan.get("all_signals", {})
    n_data = social_scan.get("symbols_with_data", 0)

    # Sub-section 1: 注意力异动 (Z-score >= 2.0)
    if alerts:
        lines.append("注意力异动 (Z>=2.0):")
        for sig in alerts[:8]:
            z = sig.get("attention_zscore", 0)
            buzz = sig.get("weighted_buzz", 0)
            r_m = sig.get("reddit_mentions", 0)
            x_m = sig.get("x_mentions", 0)
            r_s = sig.get("reddit_sentiment")
            x_s = sig.get("x_sentiment")
            total_m = r_m + x_m
            if r_s is not None and x_s is not None and total_m > 0:
                sent = (r_s * r_m + x_s * x_m) / total_m
            elif r_s is not None:
                sent = r_s
            elif x_s is not None:
                sent = x_s
            else:
                sent = 0.0
            tag = "!!!" if z >= 4.0 else ""
            lines.append("  {} Z={:.1f} buzz={:.0f} sent={:+.2f} (R{}+X{}){}"
                         .format(sig["symbol"], z, buzz if buzz is not None else 0, sent, r_m, x_m, tag))
    else:
        lines.append("注意力异动: 无")

    # Sub-section 2: Buzz Score 前十
    if all_signals:
        buzz_ranked = sorted(
            [(sym, sig) for sym, sig in all_signals.items()
             if sig.get("weighted_buzz") is not None],
            key=lambda x: x[1]["weighted_buzz"],
            reverse=True,
        )[:10]
        if buzz_ranked:
            lines.append("")
            lines.append("Buzz Score Top 10:")
            for sym, sig in buzz_ranked:
                buzz = sig["weighted_buzz"]
                r_m = sig.get("reddit_mentions", 0)
                x_m = sig.get("x_mentions", 0)
                lines.append("  {:<6} buzz={:>6.1f}  (R{}+X{})".format(
                    sym, buzz, r_m, x_m))

    # Sub-section 3: 提及量前十
    if all_signals:
        mentions_ranked = sorted(
            [(sym, sig) for sym, sig in all_signals.items()
             if sig.get("combined_mentions", 0) > 0],
            key=lambda x: x[1]["combined_mentions"],
            reverse=True,
        )[:10]
        if mentions_ranked:
            lines.append("")
            lines.append("提及量 Top 10:")
            for sym, sig in mentions_ranked:
                total = sig["combined_mentions"]
                r_m = sig.get("reddit_mentions", 0)
                x_m = sig.get("x_mentions", 0)
                lines.append("  {:<6} {:>5}次  (R{}+X{})".format(
                    sym, total, r_m, x_m))

    lines.append("")
    lines.append("覆盖: {}只".format(n_data))

    return "\n".join(lines)


def format_morning_report(
    indicator_summary: dict = None,
    momentum_results: dict = None,
    dv_result: dict = None,
    market_signals: dict = None,
    market_pulse: dict = None,
    trending_data: dict = None,
    social_scan: dict = None,
    elapsed: float = 0,
) -> str:
    """格式化完整晨报"""
    now = datetime.now()
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][now.weekday()]

    lines = [
        "*未来资本 晨报*",
        "{} ({}) 07:00".format(now.strftime("%Y-%m-%d"), weekday),
        "",
    ]

    indicator_summary = indicator_summary or {}
    momentum_results = momentum_results or {}

    if market_signals:
        lines.append("信号日: {} | 数据覆盖: {}/{}".format(
            market_signals.get("as_of"),
            market_signals.get("symbols_with_data", 0),
            market_signals.get("symbols_scanned", 0),
        ))
        lines.append("")

        lines.append(format_section_broad_signal(market_signals))
        lines.append("")
        lines.append(format_section_layered_pmarp(market_signals))
        lines.append("")
        lines.append(format_section_layered_dv(market_signals))
        lines.append("")
        lines.append(format_section_layered_rvol(market_signals))
        lines.append("")
    else:
        # A. PMARP
        lines.append(format_section_a(indicator_summary))
        lines.append("")

        # B. DV Acceleration
        dv_acc = momentum_results.get("dv_acceleration")
        if dv_acc is not None:
            lines.append(format_section_b(dv_acc))
            lines.append("")

        # C. RVOL Sustained
        rvol_list = momentum_results.get("rvol_sustained", [])
        lines.append(format_section_c(rvol_list))
        lines.append("")

    # D. Dollar Volume — also concept-bucketed for readability.
    if dv_result:
        lines.append(format_section_d(dv_result))
        lines.append("")

    # E. 市场情绪脉搏
    if market_pulse:
        lines.append(format_section_market_pulse(market_pulse))
        lines.append("")

    # F. 社交热门
    if trending_data:
        lines.append(format_section_trending(trending_data))
        lines.append("")

    # G. 社交情绪雷达
    if social_scan and social_scan.get("symbols_with_data", 0) > 0:
        lines.append(format_section_social(social_scan))
        lines.append("")

    # Footer
    n_scanned = (
        market_signals.get("symbols_scanned", 0)
        if market_signals
        else momentum_results.get("symbols_scanned", 0)
    )
    lines.append("扫描: {}只 | 耗时: {:.0f}s".format(n_scanned, elapsed))

    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def run_dollar_volume() -> dict:
    """运行 Dollar Volume 采集"""
    try:
        scripts_dir = str(Path(__file__).parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from collect_dollar_volume import collect_daily

        logger.info("开始采集 Dollar Volume...")
        result = collect_daily()
        logger.info("Dollar Volume 采集完成: %s", result.get("status"))
        return result
    except Exception as e:
        logger.warning("Dollar Volume 采集失败: %s", e)
        return {"rankings": [], "new_faces": []}


def main():
    parser = argparse.ArgumentParser(description="未来资本 晨报")
    parser.add_argument("--no-telegram", action="store_true", help="不推送 Telegram")
    parser.add_argument("--symbols", type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--no-social", action="store_true",
                        help="跳过社交情绪 Section G（社交数据延后采集时使用）")
    parser.add_argument("--social-only", action="store_true",
                        help="仅发送社交情绪日报（配合延后 cron 使用）")
    parser.add_argument("--image-report", action="store_true",
                        help="每个晨报 section 生成一张图片；Telegram 发送图片而不是长文本")
    parser.add_argument("--image-output-dir", type=str,
                        help="图片输出目录（默认 data/scans/morning_images_<timestamp>）")
    args = parser.parse_args()

    # --social-only: 仅发送社交情绪日报（独立 cron 调用）
    if args.social_only:
        logger.info("=" * 60)
        logger.info("社交情绪日报 开始")
        logger.info("=" * 60)
        start_time = time.time()
        try:
            if args.symbols:
                symbols = [s.strip().upper() for s in args.symbols.split(",")]
            else:
                symbols = get_symbols()

            # Section E + F: 市场级社交数据 (Adanos market-level)
            from src.data.market_store import get_store
            from datetime import timezone, timedelta
            store = get_store()
            now_utc = datetime.now(timezone.utc)
            today_utc = now_utc.strftime("%Y-%m-%d")
            yesterday_utc = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
            fresh_dates = {today_utc, yesterday_utc}

            market_pulse = None
            pulse = {}
            for src in ["reddit", "x"]:
                row = store.get_latest_market_sentiment(source=src)
                if row and row.get("date") in fresh_dates:
                    pulse[src] = row
            if pulse:
                market_pulse = pulse
                logger.info("市场情绪脉搏: %s", list(pulse.keys()))

            trending_data = None
            t_data = {"stocks": [], "sectors": []}
            trending_date = None
            for candidate_date in [today_utc, yesterday_utc]:
                for src in ["reddit", "x"]:
                    t_data["stocks"].extend(store.get_social_trending(candidate_date, src))
                    t_data["sectors"].extend(store.get_social_trending_sectors(candidate_date, src))
                if t_data["stocks"] or t_data["sectors"]:
                    trending_date = candidate_date
                    break
                t_data = {"stocks": [], "sectors": []}
            if t_data["stocks"] or t_data["sectors"]:
                t_data["date"] = trending_date
                trending_data = t_data
                logger.info("社交热门: %d stocks, %d sectors", len(t_data["stocks"]), len(t_data["sectors"]))

            # Section G: per-stock 社交情绪雷达
            from src.indicators.social_attention import scan_social_signals
            social_scan = scan_social_signals(symbols)
            logger.info("社交情绪扫描完成: %d 只有数据", social_scan.get("symbols_with_data", 0))

            # 组装消息: E + F + G
            sections = []
            if market_pulse:
                sections.append(format_section_market_pulse(market_pulse))
            if trending_data:
                sections.append(format_section_trending(trending_data))
            sections.append(format_section_social(social_scan))

            social_msg = "*未来资本 社交情绪日报*\n{}\n\n{}".format(
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                "\n\n".join(sections),
            )

            if not args.no_telegram:
                _send_group_message(social_msg)
            else:
                print(social_msg)
        except Exception as e:
            logger.error("社交情绪日报异常: %s", e)
            if not args.no_telegram:
                _send_group_message("*社交情绪日报异常*\n\n错误: {}".format(str(e)[:200]))

        elapsed = time.time() - start_time
        logger.info("社交情绪日报完成，耗时 %.1f 秒", elapsed)
        logger.info("=" * 60)
        return

    logger.info("=" * 60)
    logger.info("未来资本 晨报 开始")
    logger.info("=" * 60)

    start_time = time.time()

    try:
        # 1. 获取股票列表
        symbols_override = None
        if args.symbols:
            symbols_override = [s.strip().upper() for s in args.symbols.split(",")]
            symbols = symbols_override
        else:
            symbols = get_symbols()
        logger.info("股票池: %d 只", len(symbols))

        # 2. 广义市场技术信号（broad universe + market.db 价格）
        market_signals = build_market_signal_report(symbols_override=symbols_override)
        logger.info(
            "市场信号完成: scanned=%d data=%d",
            market_signals.get("symbols_scanned", 0),
            market_signals.get("symbols_with_data", 0),
        )

        # 3. Dollar Volume 采集
        dv_result = run_dollar_volume()

        # 4. 市场情绪脉搏 + 社交热门 (Adanos market-level)
        market_pulse = None
        trending_data = None
        if not args.no_social:
            try:
                from src.data.market_store import get_store
                from datetime import timezone, timedelta
                store = get_store()
                now_utc = datetime.now(timezone.utc)
                today_utc = now_utc.strftime("%Y-%m-%d")
                yesterday_utc = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
                fresh_dates = {today_utc, yesterday_utc}

                # Market sentiment (Reddit + X) — accept latest within 2 days
                pulse = {}
                for src in ["reddit", "x"]:
                    row = store.get_latest_market_sentiment(source=src)
                    if row and row.get("date") in fresh_dates:
                        pulse[src] = row
                if pulse:
                    market_pulse = pulse
                    dates_seen = set(r.get("date") for r in pulse.values())
                    logger.info("市场情绪脉搏: %s (data: %s)", list(pulse.keys()), dates_seen)

                # Trending stocks + sectors — try today first, fallback to yesterday
                t_data = {"stocks": [], "sectors": []}
                trending_date = None
                for candidate_date in [today_utc, yesterday_utc]:
                    for src in ["reddit", "x"]:
                        t_data["stocks"].extend(store.get_social_trending(candidate_date, src))
                        t_data["sectors"].extend(store.get_social_trending_sectors(candidate_date, src))
                    if t_data["stocks"] or t_data["sectors"]:
                        trending_date = candidate_date
                        break
                    # Reset for next candidate
                    t_data = {"stocks": [], "sectors": []}
                if t_data["stocks"] or t_data["sectors"]:
                    t_data["date"] = trending_date
                    trending_data = t_data
                    logger.info("社交热门: %d stocks, %d sectors (data: %s)",
                                len(t_data["stocks"]), len(t_data["sectors"]), trending_date)
            except Exception as e:
                logger.warning("市场级社交数据加载失败: %s", e)

        # 5. 社交情绪雷达（--no-social 时跳过）
        social_scan = None
        if not args.no_social:
            try:
                from src.indicators.social_attention import scan_social_signals
                logger.info("开始社交情绪扫描...")
                social_scan = scan_social_signals(symbols)
                logger.info("社交情绪扫描完成: %d 只有数据", social_scan.get("symbols_with_data", 0))
            except Exception as e:
                logger.warning("社交情绪扫描失败: %s", e)
        else:
            logger.info("跳过社交情绪（--no-social），将由 10:20 社交日报独立发送")

        elapsed = time.time() - start_time

        # 6. 格式化
        daily_msg = format_morning_report(
            dv_result=dv_result, market_signals=market_signals,
            market_pulse=market_pulse, trending_data=trending_data,
            social_scan=social_scan, elapsed=elapsed)

        image_paths = []
        if args.image_report:
            image_paths = render_morning_report_images(
                market_signals=market_signals,
                dv_result=dv_result,
                output_dir=args.image_output_dir,
            )
            logger.info("晨报图片已生成: %d 张", len(image_paths))

        # 7. 保存 JSON
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = SCANS_DIR / "morning_{}.json".format(timestamp)
        save_data = {
            "timestamp": timestamp,
            "symbols_scanned": market_signals.get("symbols_scanned", len(symbols)),
            "elapsed": round(elapsed, 1),
            "market_signals": market_signals,
        }
        if image_paths:
            save_data["image_report_paths"] = [str(path) for path in image_paths]
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("结果已保存: %s", save_path)

        # 8. 发送 Telegram
        if not args.no_telegram:
            if args.image_report and image_paths:
                _send_group_image_report(image_paths)
            else:
                _send_group_report(daily_msg)
        else:
            if args.image_report and image_paths:
                print("\n".join(str(path) for path in image_paths))
            else:
                print(daily_msg)

    except Exception as e:
        logger.error("晨报异常: %s", e)
        import traceback
        traceback.print_exc()

        if not args.no_telegram:
            error_msg = "*未来资本 晨报异常*\n\n错误: {}".format(str(e)[:200])
            _send_group_message(error_msg)

    elapsed = time.time() - start_time
    logger.info("晨报完成，耗时 %.1f 秒", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
