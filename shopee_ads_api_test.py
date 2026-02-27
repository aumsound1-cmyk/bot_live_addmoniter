#!/usr/bin/env python3
"""
Shopee Ads API - Endpoint Discovery & Testing Script
=====================================================
ใช้สำหรับทดสอบ Shopee Ads API endpoints
- ดึงข้อมูล ads campaigns
- ดึง balance/credit
- ทดสอบ set budget

วิธีใช้:
    1. ใส่ cookie string ของ creator.shopee.co.th ในตัวแปร COOKIE_STR
    2. รัน: python3 shopee_ads_api_test.py
"""

import requests
import json
import time
from datetime import datetime

# ============================================
# CONFIGURATION - ใส่ cookie ตรงนี้
# ============================================
COOKIE_STR = ""  # <-- ใส่ cookie จาก browser ตรงนี้ (จาก creator.shopee.co.th)

# ============================================
# Shopee Creator API endpoints ที่ต้องทดสอบ
# ============================================
# Base: creator.shopee.co.th
# หรือ ads.shopee.co.th
# หรือ seller.shopee.co.th
#
# Pattern ที่ระบบเดิมใช้: /supply/api/lm/sellercenter/...
# Pattern ที่ Ads น่าจะใช้: อาจเป็น /supply/api/marketing/... หรือ /api/v2/ads/...

# Endpoints ที่จะทดสอบ (จากหลายแหล่ง)
TEST_ENDPOINTS = {
    # ===== Creator Platform Ads Endpoints (น่าจะเป็นตัวเดียวกับ Live) =====
    "creator_user_info": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/userInfo",
        "method": "GET",
        "params": {},
        "desc": "ข้อมูลผู้ใช้ (ทดสอบ auth)"
    },
    
    # ===== Shopee Ads Dashboard (จาก screenshot ที่ผู้ใช้ส่งมา) =====
    # screenshot แสดง: เครดิตโฆษณา, ค่าโฆษณาวันนี้, การเข้าชม, คำสั่งซื้อ, ROAS
    # URL pattern จาก ads.shopee.co.th
    
    "ads_credit_balance": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/balance",
        "method": "GET",
        "params": {},
        "desc": "เครดิตโฆษณา (ลอง pattern 1)"
    },
    "ads_credit_balance_v2": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/credit",
        "method": "GET",
        "params": {},
        "desc": "เครดิตโฆษณา (ลอง pattern 2)"
    },
    
    # Ads Campaign List
    "ads_campaign_list": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/campaignList",
        "method": "GET",
        "params": {"page": 1, "pageSize": 50},
        "desc": "รายการ campaign (ลอง pattern 1)"
    },
    "ads_campaign_list_v2": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/campaigns",
        "method": "GET",
        "params": {"page": 1, "pageSize": 50},
        "desc": "รายการ campaign (ลอง pattern 2)"
    },
    
    # Ads Dashboard / Realtime
    "ads_dashboard": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/dashboard",
        "method": "GET",
        "params": {},
        "desc": "Ads dashboard overview"
    },
    "ads_realtime": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/realtime",
        "method": "GET",
        "params": {},
        "desc": "Ads realtime data"
    },
    
    # Live Ads (จาก screenshot - มี tab "Live Ads")
    "live_ads": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/realtime/ads",
        "method": "GET",
        "params": {},
        "desc": "Live Ads data (ลอง pattern 1)"
    },
    "live_ads_v2": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/realtime/dashboard/ads",
        "method": "GET",
        "params": {},
        "desc": "Live Ads data (ลอง pattern 2)"
    },
    "live_ads_v3": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/realtime/liveAds",
        "method": "GET",
        "params": {},
        "desc": "Live Ads data (ลอง pattern 3)"
    },
    
    # Ads Performance / Stats
    "ads_performance": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/performance",
        "method": "GET",
        "params": {},
        "desc": "Ads performance"
    },
    "ads_stats": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/stats",
        "method": "GET",
        "params": {},
        "desc": "Ads statistics"
    },
    
    # ===== Shopee Ads Platform (ads.shopee.co.th) =====
    "ads_platform_balance": {
        "url": "https://ads.shopee.co.th/api/v1/balance",
        "method": "GET",
        "params": {},
        "desc": "Ads platform balance"
    },
    "ads_platform_campaigns": {
        "url": "https://ads.shopee.co.th/api/v1/campaigns",
        "method": "GET",
        "params": {"page": 1, "pageSize": 50},
        "desc": "Ads platform campaigns"
    },
    
    # ===== Seller Center Ads (PAS = Product Ads Service) =====
    "seller_ads_balance": {
        "url": "https://seller.shopee.co.th/api/marketing/v3/pas/balance/",
        "method": "GET",
        "params": {},
        "desc": "Seller center ads balance"
    },
    "seller_ads_campaigns": {
        "url": "https://seller.shopee.co.th/api/marketing/v3/pas/campaign/list/",
        "method": "GET",
        "params": {},
        "desc": "Seller center ads campaign list"
    },
    "seller_ads_live": {
        "url": "https://seller.shopee.co.th/api/marketing/v3/pas/live_streaming/campaign/",
        "method": "GET",
        "params": {},
        "desc": "Seller center live streaming ads"
    },

    # ===== Creator ads with session =====
    "creator_ads_session": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/realtime/dashboard/adsInfo",
        "method": "GET",
        "params": {},
        "desc": "Creator realtime ads info"
    },
    "creator_ads_overview": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/overview",
        "method": "GET",
        "params": {},
        "desc": "Creator ads overview"
    },
    "creator_ads_live_list": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/liveAdsList",
        "method": "GET",
        "params": {"page": 1, "pageSize": 50},
        "desc": "Creator live ads list"
    },
    "creator_ads_account_balance": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/accountBalance",
        "method": "GET",
        "params": {},
        "desc": "Creator ads account balance"
    },
}

# Budget-setting endpoints to try
BUDGET_TEST_ENDPOINTS = {
    "set_budget_v1": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/setBudget",
        "method": "POST",
        "desc": "Set budget (creator pattern)"
    },
    "set_budget_v2": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/campaign/updateBudget",
        "method": "POST",
        "desc": "Update budget (creator pattern v2)"
    },
    "seller_set_budget": {
        "url": "https://seller.shopee.co.th/api/marketing/v3/pas/campaign/update_daily_budget/",
        "method": "POST",
        "desc": "Seller center update daily budget"
    },
    "creator_set_budget": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/updateBudget",
        "method": "POST",
        "desc": "Creator ads update budget"
    },
    "creator_pause": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/pauseCampaign",
        "method": "POST",
        "desc": "Creator ads pause campaign"
    },
    "creator_resume": {
        "url": "https://creator.shopee.co.th/supply/api/lm/sellercenter/ads/resumeCampaign",
        "method": "POST",
        "desc": "Creator ads resume campaign"
    },
}


def parse_cookies(cookie_str):
    """Parse cookie string to dict"""
    cookies = {}
    if not cookie_str:
        return cookies
    for cookie in cookie_str.split('; '):
        if '=' in cookie:
            key, value = cookie.split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies


def get_headers(cookies):
    """Build headers with CSRF token"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/json',
        'x-csrftoken': cookies.get('csrftoken', ''),
        'Referer': 'https://creator.shopee.co.th/insight/live/list',
        'Accept': 'application/json',
    }


def test_endpoint(name, config, headers, cookies):
    """Test a single API endpoint"""
    url = config['url']
    method = config['method']
    params = config.get('params', {})
    desc = config.get('desc', '')
    
    print(f"\n{'='*60}")
    print(f"[TEST] {name}")
    print(f"  URL: {url}")
    print(f"  Method: {method}")
    print(f"  Desc: {desc}")
    
    try:
        if method == 'GET':
            resp = requests.get(url, headers=headers, cookies=cookies, params=params, timeout=10)
        else:
            resp = requests.post(url, headers=headers, cookies=cookies, json=params, timeout=10)
        
        print(f"  Status: {resp.status_code}")
        
        # Try parse JSON
        try:
            data = resp.json()
            # Show first 500 chars
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            if len(json_str) > 500:
                print(f"  Response (first 500 chars):\n{json_str[:500]}...")
            else:
                print(f"  Response:\n{json_str}")
            
            # Check for success indicators
            if data.get('code') == 0 or data.get('success') == True or 'data' in data:
                print(f"  >>> SUCCESS! This endpoint works <<<")
                return True, data
            elif data.get('code') and data.get('code') != 0:
                print(f"  >>> API returned error code: {data.get('code')} / msg: {data.get('msg', data.get('message', ''))}")
                return False, data
        except:
            text = resp.text[:300]
            print(f"  Response (not JSON): {text}")
        
        if resp.status_code == 200:
            return True, resp.text
        return False, None
        
    except requests.exceptions.ConnectionError as e:
        print(f"  Error: Connection failed - {str(e)[:100]}")
        return False, None
    except requests.exceptions.Timeout:
        print(f"  Error: Timeout")
        return False, None
    except Exception as e:
        print(f"  Error: {str(e)[:100]}")
        return False, None


def main():
    print("=" * 60)
    print("Shopee Ads API - Endpoint Discovery Tool")
    print("=" * 60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if not COOKIE_STR:
        print("\n[ERROR] กรุณาใส่ cookie string ในตัวแปร COOKIE_STR")
        print("วิธีหา cookie:")
        print("  1. เปิด Chrome DevTools (F12)")
        print("  2. ไปที่ Network tab")
        print("  3. เข้า creator.shopee.co.th")
        print("  4. คลิกที่ request ใดก็ได้")
        print("  5. ดูที่ Request Headers > Cookie")
        print("  6. Copy ทั้ง string มาใส่")
        return
    
    cookies = parse_cookies(COOKIE_STR)
    headers = get_headers(cookies)
    
    print(f"\nCookies loaded: {len(cookies)} entries")
    print(f"CSRF Token: {cookies.get('csrftoken', 'NOT FOUND')[:20]}...")
    
    # ===== Phase 1: Test read endpoints =====
    print("\n" + "=" * 60)
    print("PHASE 1: Testing READ endpoints (GET)")
    print("=" * 60)
    
    working_endpoints = {}
    
    for name, config in TEST_ENDPOINTS.items():
        success, data = test_endpoint(name, config, headers, cookies)
        if success:
            working_endpoints[name] = config
        time.sleep(0.5)  # Rate limiting
    
    # ===== Summary =====
    print("\n" + "=" * 60)
    print("SUMMARY - Working Endpoints")
    print("=" * 60)
    
    if working_endpoints:
        for name, config in working_endpoints.items():
            print(f"  [OK] {name}: {config['url']}")
    else:
        print("  No working endpoints found!")
        print("\n  Possible reasons:")
        print("  1. Cookie หมดอายุ - ลอง login ใหม่แล้ว copy cookie ใหม่")
        print("  2. ต้องใช้ cookie จาก seller.shopee.co.th แทน creator")
        print("  3. Ads API อาจใช้ domain อื่น")
    
    # ===== Phase 2: Show budget test endpoints =====
    print("\n" + "=" * 60)
    print("PHASE 2: Budget Setting Endpoints (NOT tested - ใช้ด้วยความระวัง)")
    print("=" * 60)
    print("  เหล่านี้เป็น POST endpoints สำหรับตั้งงบ")
    print("  *** ไม่ได้ทดสอบอัตโนมัติ เพื่อความปลอดภัย ***")
    print("  *** ต้องทดสอบ manual กับ campaign จริง ***\n")
    
    for name, config in BUDGET_TEST_ENDPOINTS.items():
        print(f"  [{config['method']}] {name}")
        print(f"    URL: {config['url']}")
        print(f"    Desc: {config['desc']}")
        print()
    
    print("=" * 60)
    print("เสร็จสิ้นการทดสอบ")
    print("=" * 60)


if __name__ == '__main__':
    main()
