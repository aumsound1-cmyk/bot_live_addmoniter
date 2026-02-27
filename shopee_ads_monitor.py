#!/usr/bin/env python3
"""
Shopee Ads Monitor - Backend Script
====================================
ระบบ Monitor และควบคุม Ads อัตโนมัติ

Features:
    - ดึงข้อมูล Ads จาก Shopee API ทุก 2-5 นาที
    - เก็บ Snapshot ทุก 5 นาที สำหรับวัดผล 15/60/180 นาที
    - Auto Budget Engine:
        Type 1 (ปกติ): ROAS + รถเข็น evaluation
        Type 2 (แข่งขันสูง): รถเข็นเป็นหลัก + เพิ่มงบทุก X นาที
    - Upload ข้อมูลไป Firebase Realtime Database
    - อ่านข้อมูล clicks/cart/orders/sales จาก shopee_monitor (ระบบเดิม)

Usage:
    python3 shopee_ads_monitor.py

Config:
    - Cookie: ดึงจาก Google Sheets (เหมือนระบบเดิม)
    - Firebase: ใช้ service account key เดียวกัน
"""

import requests
import json
import time
import math
import logging
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import pandas as pd
except ImportError:
    print("pip install pandas")
    exit(1)

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:
    print("pip install firebase-admin")
    exit(1)


# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('ads_monitor')


# ============================================
# CONFIGURATION
# ============================================
class Config:
    # Firebase
    FIREBASE_KEY_FILE = 'monitershopee-firebase-key.json'
    FIREBASE_DB_URL = 'https://monitershopee-default-rtdb.asia-southeast1.firebasedatabase.app'
    
    # Google Sheets (same as main monitor)
    SHEET_ID = '1b2zDX1SOf57Sr1s3aNUbxatR9UJHtwdoPSv4XnUNGX4'
    SHEET_GID_COINS = '1151548269'      # Account metadata + cookies
    SHEET_GID_COMMISSION = '843755068'   # Commission data
    
    # Timing
    FETCH_INTERVAL_SEC = 180   # 3 minutes (2-5 min range)
    SNAPSHOT_INTERVAL_SEC = 300  # 5 minutes
    
    # Budget Rules
    MIN_BUDGET = 200           # Minimum budget (Baht)
    BUDGET_INCREMENT = 25      # Minimum increment
    VALID_ENDINGS = [0, 25, 50, 75]  # Valid budget endings
    
    # Shopee API
    SHOPEE_CREATOR_BASE = 'https://creator.shopee.co.th'
    SHOPEE_SELLER_BASE = 'https://seller.shopee.co.th'
    
    # API Endpoints (จะถูก update เมื่อทดสอบเจอ endpoint ที่ใช้ได้)
    # ===================================================================
    # หมายเหตุ: Shopee Ads API ไม่มี public documentation
    # ต้องทดสอบหา endpoint ที่ถูกต้องก่อนใช้งาน
    # ใช้ shopee_ads_api_test.py เพื่อทดสอบ
    # ===================================================================
    
    # Endpoint ที่ใช้ได้ (ใส่หลังจากทดสอบแล้ว)
    ADS_BALANCE_URL = ''           # เช่น '/supply/api/lm/sellercenter/ads/balance'
    ADS_CAMPAIGN_LIST_URL = ''     # เช่น '/supply/api/lm/sellercenter/ads/campaignList'
    ADS_SET_BUDGET_URL = ''        # เช่น '/supply/api/lm/sellercenter/ads/setBudget'
    ADS_PAUSE_CAMPAIGN_URL = ''    # เช่น '/supply/api/lm/sellercenter/ads/pause'
    ADS_RESUME_CAMPAIGN_URL = ''   # เช่น '/supply/api/lm/sellercenter/ads/resume'


# ============================================
# FIREBASE MANAGER
# ============================================
class FirebaseManager:
    def __init__(self):
        self.initialized = False
    
    def init(self):
        try:
            firebase_admin.get_app()
            self.initialized = True
        except ValueError:
            try:
                cred = credentials.Certificate(Config.FIREBASE_KEY_FILE)
                firebase_admin.initialize_app(cred, {
                    'databaseURL': Config.FIREBASE_DB_URL
                })
                self.initialized = True
                log.info('Firebase connected')
            except Exception as e:
                log.error(f'Firebase init failed: {e}')
                self.initialized = False
        return self.initialized
    
    def get_ref(self, path):
        return db.reference(path)
    
    def read(self, path):
        try:
            return self.get_ref(path).get() or {}
        except Exception as e:
            log.error(f'Firebase read error ({path}): {e}')
            return {}
    
    def write(self, path, data):
        try:
            self.get_ref(path).set(data)
            return True
        except Exception as e:
            log.error(f'Firebase write error ({path}): {e}')
            return False
    
    def update(self, path, data):
        try:
            self.get_ref(path).update(data)
            return True
        except Exception as e:
            log.error(f'Firebase update error ({path}): {e}')
            return False
    
    def push(self, path, data):
        try:
            self.get_ref(path).push(data)
            return True
        except Exception as e:
            log.error(f'Firebase push error ({path}): {e}')
            return False


# ============================================
# SHOPEE API CLIENT
# ============================================
class ShopeeAdsClient:
    """Client for Shopee Ads API calls"""
    
    def __init__(self):
        self.session = requests.Session()
    
    def parse_cookies(self, cookie_str):
        cookies = {}
        if not cookie_str:
            return cookies
        for cookie in str(cookie_str).split('; '):
            if '=' in cookie:
                key, value = cookie.split('=', 1)
                cookies[key.strip()] = value.strip()
        return cookies
    
    def get_headers(self, cookies):
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/json',
            'x-csrftoken': cookies.get('csrftoken', ''),
            'Referer': 'https://creator.shopee.co.th/insight/live/list',
            'Accept': 'application/json',
        }
    
    def verify_auth(self, cookie_str):
        """Verify cookie is valid by calling userInfo endpoint"""
        cookies = self.parse_cookies(cookie_str)
        headers = self.get_headers(cookies)
        
        try:
            resp = self.session.get(
                f'{Config.SHOPEE_CREATOR_BASE}/supply/api/lm/sellercenter/userInfo',
                headers=headers,
                cookies=cookies,
                timeout=10
            )
            data = resp.json()
            if data.get('data'):
                username = data['data'].get('userName', data['data'].get('name', 'unknown'))
                return True, username
        except Exception as e:
            log.error(f'Auth verify failed: {e}')
        return False, None
    
    def get_ads_balance(self, cookie_str):
        """Get ads credit balance"""
        if not Config.ADS_BALANCE_URL:
            return None
        
        cookies = self.parse_cookies(cookie_str)
        headers = self.get_headers(cookies)
        
        try:
            url = Config.SHOPEE_CREATOR_BASE + Config.ADS_BALANCE_URL
            resp = self.session.get(url, headers=headers, cookies=cookies, timeout=10)
            data = resp.json()
            if data.get('data') is not None:
                return data['data']
        except Exception as e:
            log.error(f'Get ads balance failed: {e}')
        return None
    
    def get_ads_campaigns(self, cookie_str):
        """Get list of ads campaigns"""
        if not Config.ADS_CAMPAIGN_LIST_URL:
            return []
        
        cookies = self.parse_cookies(cookie_str)
        headers = self.get_headers(cookies)
        
        try:
            url = Config.SHOPEE_CREATOR_BASE + Config.ADS_CAMPAIGN_LIST_URL
            resp = self.session.get(
                url,
                headers=headers,
                cookies=cookies,
                params={'page': 1, 'pageSize': 100},
                timeout=10
            )
            data = resp.json()
            if data.get('data'):
                return data['data'].get('list', data['data'])
        except Exception as e:
            log.error(f'Get ads campaigns failed: {e}')
        return []
    
    def set_campaign_budget(self, cookie_str, campaign_id, new_budget):
        """Set campaign daily budget"""
        if not Config.ADS_SET_BUDGET_URL:
            log.warning('ADS_SET_BUDGET_URL not configured')
            return False
        
        cookies = self.parse_cookies(cookie_str)
        headers = self.get_headers(cookies)
        
        try:
            url = Config.SHOPEE_CREATOR_BASE + Config.ADS_SET_BUDGET_URL
            payload = {
                'campaignId': campaign_id,
                'dailyBudget': new_budget,
            }
            resp = self.session.post(
                url,
                headers=headers,
                cookies=cookies,
                json=payload,
                timeout=10
            )
            data = resp.json()
            if data.get('code') == 0 or data.get('success'):
                log.info(f'Budget set: campaign={campaign_id}, budget={new_budget}')
                return True
            else:
                log.error(f'Set budget failed: {data}')
        except Exception as e:
            log.error(f'Set budget error: {e}')
        return False
    
    def pause_campaign(self, cookie_str, campaign_id):
        """Pause a campaign"""
        if not Config.ADS_PAUSE_CAMPAIGN_URL:
            log.warning('ADS_PAUSE_CAMPAIGN_URL not configured')
            return False
        
        cookies = self.parse_cookies(cookie_str)
        headers = self.get_headers(cookies)
        
        try:
            url = Config.SHOPEE_CREATOR_BASE + Config.ADS_PAUSE_CAMPAIGN_URL
            resp = self.session.post(
                url,
                headers=headers,
                cookies=cookies,
                json={'campaignId': campaign_id},
                timeout=10
            )
            data = resp.json()
            return data.get('code') == 0 or data.get('success')
        except Exception as e:
            log.error(f'Pause campaign error: {e}')
        return False
    
    def resume_campaign(self, cookie_str, campaign_id):
        """Resume a paused campaign"""
        if not Config.ADS_RESUME_CAMPAIGN_URL:
            log.warning('ADS_RESUME_CAMPAIGN_URL not configured')
            return False
        
        cookies = self.parse_cookies(cookie_str)
        headers = self.get_headers(cookies)
        
        try:
            url = Config.SHOPEE_CREATOR_BASE + Config.ADS_RESUME_CAMPAIGN_URL
            resp = self.session.post(
                url,
                headers=headers,
                cookies=cookies,
                json={'campaignId': campaign_id},
                timeout=10
            )
            data = resp.json()
            return data.get('code') == 0 or data.get('success')
        except Exception as e:
            log.error(f'Resume campaign error: {e}')
        return False


# ============================================
# DATA LOADER (Google Sheets)
# ============================================
class DataLoader:
    """Load channel data from Google Sheets (same as main monitor)"""
    
    @staticmethod
    def load_channel_data():
        """Load data_dict from Google Sheets"""
        data_dict = {}
        
        try:
            # Sheet 1: Coins (metadata + cookies)
            url = f"https://docs.google.com/spreadsheets/d/{Config.SHEET_ID}/export?format=csv&gid={Config.SHEET_GID_COINS}"
            df = pd.read_csv(url)
            
            fields = ["no", "black", "cookie", "last_cookie", "sim", "server",
                      "basket", "phone", "status", "day", "admin", "data"]
            field_cols = []
            name_col = None
            
            # Find column indices (headers in row 8)
            for x in range(len(df.iloc[8])):
                val = str(df.iloc[8, x]).strip()
                if val in fields:
                    field_cols.append(x)
                elif val == "name":
                    name_col = x
            
            if name_col is None:
                log.error('Column "name" not found in sheet')
                return {}
            
            # Build data_dict
            for i in range(8, min(10010, len(df))):
                try:
                    r_name = df.iloc[i, name_col]
                    if pd.notna(r_name) and str(r_name).strip():
                        r_name = str(r_name).strip()
                        data_dict[r_name] = {}
                        for x in field_cols:
                            col_name = str(df.iloc[8, x]).strip()
                            val = df.iloc[i, x] if pd.notna(df.iloc[i, x]) else ""
                            data_dict[r_name][col_name] = str(val).strip()
                except Exception:
                    pass
            
            log.info(f'Loaded {len(data_dict)} channels from Google Sheets')
            
        except Exception as e:
            log.error(f'Failed to load channel data: {e}')
        
        return data_dict


# ============================================
# BUDGET LOGIC
# ============================================
class BudgetManager:
    """Budget validation and calculation"""
    
    @staticmethod
    def validate(amount):
        if amount < Config.MIN_BUDGET:
            return False, f'ขั้นต่ำ {Config.MIN_BUDGET} บาท'
        remainder = amount % 100
        if remainder not in Config.VALID_ENDINGS:
            return False, 'ต้องลงท้ายด้วย 0, 25, 50 หรือ 75'
        return True, 'OK'
    
    @staticmethod
    def round_up(amount):
        """Round up to valid budget"""
        base = math.ceil(amount / 25) * 25
        return max(Config.MIN_BUDGET, base)
    
    @staticmethod
    def calc_increment(current_budget, increment=None):
        """Calculate next budget with increment"""
        inc = increment or Config.BUDGET_INCREMENT
        new_budget = BudgetManager.round_up(current_budget + inc)
        return new_budget


# ============================================
# AUTO-BUDGET ENGINE
# ============================================
class AutoBudgetEngine:
    """Auto budget rules engine - runs on backend"""
    
    def __init__(self, firebase, api_client):
        self.fb = firebase
        self.api = api_client
    
    def evaluate_all(self, campaigns, snapshots, live_data):
        """Evaluate all campaigns and take actions"""
        actions = []
        
        for cam_id, cam in campaigns.items():
            if cam.get('auto_enabled') == False:
                continue
            
            if cam.get('campaign_type') == 'competition':
                action = self.evaluate_competition(cam_id, cam, snapshots, live_data)
            else:
                action = self.evaluate_normal(cam_id, cam, snapshots, live_data)
            
            if action:
                actions.append(action)
        
        return actions
    
    def evaluate_normal(self, cam_id, cam, snapshots, live_data):
        """
        Type 1: ปกติ
        - งบ >= 90% + ROAS ดี -> เพิ่มงบ
        - งบ >= 90% + รถเข็นดี (15/60/180 นาที) -> เพิ่มงบ
        - ROAS < 50% เป้า -> freeze/pause
        - ช่วงเวลา 06:00,11:30,18:00,22:00 -> เปิด/เพิ่ม
        """
        spent = float(cam.get('spent_today', 0))
        budget = float(cam.get('daily_budget', 200))
        roas = float(cam.get('roas', 0))
        roas_target = float(cam.get('roas_target', 30))
        cart_value = float(cam.get('cart_value', 5))
        budget_threshold = float(cam.get('budget_threshold', 90)) / 100
        roas_min_pct = float(cam.get('roas_min_pct', 50)) / 100
        pct_used = spent / budget if budget > 0 else 0
        
        channel = cam.get('channel', cam_id)
        
        # 1. Check ROAS too low
        if roas > 0 and roas < roas_target * roas_min_pct:
            if spent > 200:
                # Freeze: set budget = spent (rounded)
                freeze_budget = BudgetManager.round_up(spent)
                if budget != freeze_budget:
                    return {
                        'campaign_id': cam_id,
                        'action': 'set_budget',
                        'new_budget': freeze_budget,
                        'reason': f'ROAS ต่ำ ({roas:.1f} < {roas_target * roas_min_pct:.0f}) freeze งบ',
                        'channel': channel
                    }
            elif spent < 200:
                # Pause
                if cam.get('status') != 'paused':
                    return {
                        'campaign_id': cam_id,
                        'action': 'pause',
                        'reason': f'ROAS ต่ำ ({roas:.1f}) ใช้ไปน้อยกว่า 200 -> หยุด',
                        'channel': channel
                    }
            return None
        
        # 2. Budget >= threshold -> evaluate for increase
        if pct_used >= budget_threshold:
            # Check ROAS good
            if roas >= roas_target:
                new_budget = BudgetManager.calc_increment(budget)
                return {
                    'campaign_id': cam_id,
                    'action': 'increase_budget',
                    'new_budget': new_budget,
                    'reason': f'ROAS ดี ({roas:.1f} >= {roas_target}) งบใช้ {pct_used*100:.0f}%',
                    'channel': channel
                }
            
            # Check cart performance
            cam_snaps = snapshots.get(cam_id, {})
            live = self._find_live_data(channel, live_data)
            
            windows = []
            if cam.get('eval_180') != False:
                windows.append(180)
            if cam.get('eval_60') != False:
                windows.append(60)
            if cam.get('eval_15') != False:
                windows.append(15)
            
            for mins in windows:
                if self._is_cart_good(cam_snaps, cart_value, mins):
                    new_budget = BudgetManager.calc_increment(budget)
                    return {
                        'campaign_id': cam_id,
                        'action': 'increase_budget',
                        'new_budget': new_budget,
                        'reason': f'รถเข็นดีใน {mins} นาที งบใช้ {pct_used*100:.0f}%',
                        'channel': channel
                    }
        
        # 3. Check scheduled reactivation
        if cam.get('status') in ('budget_full', 'paused'):
            sched = self._check_schedule(cam)
            if sched:
                return sched
        
        return None
    
    def evaluate_competition(self, cam_id, cam, snapshots, live_data):
        """
        Type 2: แข่งขันสูง
        - วัดรถเข็น 15 นาที
        - งบเต็ม -> เพิ่ม 25 บาท ทุก X นาที
        - ช่วงไม่เพิ่ม (เช่น 03:00-05:00)
        """
        spent = float(cam.get('spent_today', 0))
        budget = float(cam.get('daily_budget', 200))
        pct_used = spent / budget if budget > 0 else 0
        channel = cam.get('channel', cam_id)
        
        # Check no-increase time window
        now = datetime.now()
        no_inc_start = cam.get('no_increase_start', '03:00')
        no_inc_end = cam.get('no_increase_end', '05:00')
        if self._in_time_window(now, no_inc_start, no_inc_end):
            return None
        
        # Budget full
        if pct_used >= 0.99 or cam.get('status') == 'budget_full':
            interval = int(cam.get('competition_interval', 30))
            last_action = cam.get('last_auto_action', 0)
            elapsed = (time.time() * 1000 - float(last_action)) / 60000 if last_action else 999
            
            if elapsed >= interval:
                cam_snaps = snapshots.get(cam_id, {})
                cart_value = float(cam.get('cart_value', 5))
                amount = int(cam.get('competition_amount', 25))
                
                if self._is_cart_good(cam_snaps, cart_value, 15):
                    new_budget = BudgetManager.calc_increment(budget, amount)
                    return {
                        'campaign_id': cam_id,
                        'action': 'increase_budget',
                        'new_budget': new_budget,
                        'reason': f'แข่งขัน: รถเข็นดี 15น. +{amount}',
                        'channel': channel
                    }
                else:
                    new_budget = BudgetManager.calc_increment(budget, 25)
                    return {
                        'campaign_id': cam_id,
                        'action': 'increase_budget',
                        'new_budget': new_budget,
                        'reason': f'แข่งขัน: ตามกำหนดทุก {interval} นาที +25',
                        'channel': channel
                    }
        
        return None
    
    def _is_cart_good(self, cam_snaps, cart_value, minutes):
        """Check if cart performance is good in time window"""
        if not cam_snaps:
            return False
        
        now_ms = time.time() * 1000
        window_start = now_ms - (minutes * 60 * 1000)
        
        snaps_in_window = []
        for ts_str, data in cam_snaps.items():
            try:
                ts = int(ts_str)
                if ts >= window_start and ts <= now_ms:
                    snaps_in_window.append({'time': ts, **data})
            except (ValueError, TypeError):
                pass
        
        if len(snaps_in_window) < 2:
            return False
        
        snaps_in_window.sort(key=lambda x: x['time'])
        first = snaps_in_window[0]
        last = snaps_in_window[-1]
        
        spent_diff = float(last.get('spent', 0)) - float(first.get('spent', 0))
        cart_diff = int(last.get('cart', 0)) - int(first.get('cart', 0))
        
        if spent_diff < cart_value:
            return False
        if cart_diff <= 0:
            return False
        
        cost_per_cart = spent_diff / cart_diff
        return cost_per_cart <= cart_value * 1.5
    
    def _find_live_data(self, channel_name, live_data):
        if not channel_name or not live_data:
            return None
        name = channel_name.lower()
        for k, v in live_data.items():
            if isinstance(v, dict) and v.get('channel', '').lower() == name:
                return v
        return None
    
    def _check_schedule(self, cam):
        """Check scheduled reactivation times"""
        sched_times_str = cam.get('schedule_times', '06:00,11:30,18:00,22:00')
        sched_times = [t.strip() for t in sched_times_str.split(',')]
        
        now = datetime.now()
        now_str = now.strftime('%H:%M')
        
        for t in sched_times:
            if now_str == t:
                today_key = now.strftime('%Y-%m-%d') + '_' + t
                if cam.get('last_schedule_action') == today_key:
                    return None  # Already acted
                
                channel = cam.get('channel', '')
                cam_id = None
                # Find campaign ID
                for k, v in (self.fb.read('shopee_ads/campaigns') or {}).items():
                    if v.get('channel') == channel:
                        cam_id = k
                        break
                
                if cam.get('status') == 'budget_full':
                    new_budget = BudgetManager.calc_increment(float(cam.get('daily_budget', 200)))
                    return {
                        'campaign_id': cam_id,
                        'action': 'increase_budget',
                        'new_budget': new_budget,
                        'reason': f'กำหนดเวลา {t}: งบเต็ม +25',
                        'channel': channel,
                        'schedule_key': today_key
                    }
                elif cam.get('status') == 'paused':
                    return {
                        'campaign_id': cam_id,
                        'action': 'resume',
                        'reason': f'กำหนดเวลา {t}: เปิดใหม่',
                        'channel': channel,
                        'schedule_key': today_key
                    }
        
        return None
    
    def _in_time_window(self, now, start_str, end_str):
        try:
            sh, sm = map(int, start_str.split(':'))
            eh, em = map(int, end_str.split(':'))
            now_min = now.hour * 60 + now.minute
            start_min = sh * 60 + sm
            end_min = eh * 60 + em
            if start_min <= end_min:
                return start_min <= now_min <= end_min
            else:
                return now_min >= start_min or now_min <= end_min
        except Exception:
            return False
    
    def execute_action(self, action, cookie_str):
        """Execute a budget action via API + update Firebase"""
        cam_id = action.get('campaign_id')
        act = action.get('action')
        channel = action.get('channel', '')
        
        log.info(f"[AUTO] {channel}: {act} - {action.get('reason', '')}")
        
        # Update Firebase (always, regardless of API success)
        fb_updates = {'last_auto_action': int(time.time() * 1000)}
        
        if action.get('schedule_key'):
            fb_updates['last_schedule_action'] = action['schedule_key']
        
        if act == 'increase_budget' or act == 'set_budget':
            new_budget = action.get('new_budget', 200)
            valid, msg = BudgetManager.validate(new_budget)
            if not valid:
                log.warning(f'Invalid budget {new_budget}: {msg}')
                return False
            
            fb_updates['daily_budget'] = new_budget
            fb_updates['status'] = 'active'
            
            # Try API call (if configured)
            if Config.ADS_SET_BUDGET_URL and cookie_str:
                api_ok = self.api.set_campaign_budget(cookie_str, cam_id, new_budget)
                if not api_ok:
                    log.warning(f'API set_budget failed for {channel}, Firebase still updated')
        
        elif act == 'pause':
            fb_updates['status'] = 'paused'
            if Config.ADS_PAUSE_CAMPAIGN_URL and cookie_str:
                self.api.pause_campaign(cookie_str, cam_id)
        
        elif act == 'resume':
            fb_updates['status'] = 'active'
            if Config.ADS_RESUME_CAMPAIGN_URL and cookie_str:
                self.api.resume_campaign(cookie_str, cam_id)
        
        # Update Firebase
        if cam_id:
            self.fb.update(f'shopee_ads/campaigns/{cam_id}', fb_updates)
        
        # Log action
        self.fb.push('shopee_ads/action_log', {
            'time': datetime.now().strftime('%H:%M'),
            'type': act.replace('_budget', '').replace('increase', 'increase'),
            'channel': channel,
            'message': action.get('reason', ''),
            'timestamp': int(time.time() * 1000)
        })
        
        return True


# ============================================
# SNAPSHOT MANAGER
# ============================================
class SnapshotManager:
    """Manages snapshot data for time-window evaluation"""
    
    def __init__(self, firebase):
        self.fb = firebase
        self.last_snapshot_time = 0
    
    def should_take_snapshot(self):
        elapsed = time.time() - self.last_snapshot_time
        return elapsed >= Config.SNAPSHOT_INTERVAL_SEC
    
    def take_snapshot(self, campaigns, live_data):
        """Take snapshot of current state for all campaigns"""
        now_ms = str(int(time.time() * 1000))
        
        for cam_id, cam in campaigns.items():
            channel = cam.get('channel', '')
            live = self._find_live(channel, live_data)
            
            snap = {
                'spent': float(cam.get('spent_today', 0)),
                'cart': int(live.get('added_to_cart', live.get('cart_count', 0))) if live else int(cam.get('cart', 0)),
                'clicks': int(live.get('clicks', 0)) if live else int(cam.get('clicks', 0)),
                'orders': int(live.get('orders', 0)) if live else int(cam.get('orders', 0)),
                'sales': float(live.get('sales', 0)) if live else float(cam.get('sales', 0)),
            }
            
            self.fb.update(f'shopee_ads/snapshots/{cam_id}', {now_ms: snap})
        
        self.last_snapshot_time = time.time()
        log.info(f'Snapshot taken for {len(campaigns)} campaigns')
    
    def cleanup_old_snapshots(self, campaigns):
        """Remove snapshots older than 4 hours"""
        cutoff = str(int((time.time() - 4 * 3600) * 1000))
        
        for cam_id in campaigns:
            snaps = self.fb.read(f'shopee_ads/snapshots/{cam_id}') or {}
            for ts in list(snaps.keys()):
                if ts < cutoff:
                    try:
                        self.fb.get_ref(f'shopee_ads/snapshots/{cam_id}/{ts}').delete()
                    except Exception:
                        pass
    
    def _find_live(self, channel_name, live_data):
        if not channel_name or not live_data:
            return None
        name = channel_name.lower()
        for k, v in live_data.items():
            if isinstance(v, dict) and v.get('channel', '').lower() == name:
                return v
        return None


# ============================================
# MAIN MONITOR LOOP
# ============================================
class AdsMonitor:
    """Main monitoring loop"""
    
    def __init__(self):
        self.fb = FirebaseManager()
        self.api = ShopeeAdsClient()
        self.engine = AutoBudgetEngine(self.fb, self.api)
        self.snapshots = SnapshotManager(self.fb)
        self.data_dict = {}
        self.cycle_count = 0
    
    def start(self):
        log.info('=' * 50)
        log.info('Shopee Ads Monitor - Starting')
        log.info('=' * 50)
        
        # Init Firebase
        if not self.fb.init():
            log.error('Cannot start without Firebase')
            return
        
        # Load channel data
        self.data_dict = DataLoader.load_channel_data()
        if not self.data_dict:
            log.warning('No channel data loaded, running with Firebase data only')
        
        # Check API endpoints
        if not Config.ADS_CAMPAIGN_LIST_URL:
            log.warning('=' * 50)
            log.warning('ADS API ENDPOINTS NOT CONFIGURED!')
            log.warning('Run shopee_ads_api_test.py first to find working endpoints')
            log.warning('System will run in Firebase-only mode (no API calls)')
            log.warning('=' * 50)
        
        # Main loop
        log.info(f'Starting monitor loop (interval: {Config.FETCH_INTERVAL_SEC}s)')
        
        while True:
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                log.info('Stopped by user')
                break
            except Exception as e:
                log.error(f'Cycle error: {e}')
                traceback.print_exc()
            
            log.info(f'Waiting {Config.FETCH_INTERVAL_SEC}s until next cycle...')
            time.sleep(Config.FETCH_INTERVAL_SEC)
    
    def run_cycle(self):
        """Run one monitoring cycle"""
        self.cycle_count += 1
        cycle_start = time.time()
        log.info(f'--- Cycle #{self.cycle_count} ---')
        
        # 1. Read current campaigns from Firebase
        campaigns = self.fb.read('shopee_ads/campaigns') or {}
        log.info(f'Campaigns in Firebase: {len(campaigns)}')
        
        # 2. Read live data from main monitor (shared data - no API call)
        live_data = self.fb.read('shopee_monitor/live_shopee') or {}
        log.info(f'Live channels: {len(live_data)}')
        
        # 3. Fetch ads data from API (if configured)
        if Config.ADS_CAMPAIGN_LIST_URL:
            self._fetch_ads_data(campaigns)
        
        # 4. Merge live data (clicks/cart/orders/sales) into campaigns
        self._merge_live_data(campaigns, live_data)
        
        # 5. Take snapshot if needed
        if self.snapshots.should_take_snapshot():
            self.snapshots.take_snapshot(campaigns, live_data)
        
        # 6. Run auto-budget engine
        snapshots_data = self.fb.read('shopee_ads/snapshots') or {}
        actions = self.engine.evaluate_all(campaigns, snapshots_data, live_data)
        
        if actions:
            log.info(f'Auto-budget: {len(actions)} actions to execute')
            for action in actions:
                # Get cookie for API call
                cookie_str = self._get_cookie_for_channel(action.get('channel', ''))
                self.engine.execute_action(action, cookie_str)
        
        # 7. Update metadata
        self.fb.update('shopee_ads/metadata', {
            'last_update': datetime.now().isoformat(),
            'update_timestamp': int(time.time() * 1000),
            'total_campaigns': len(campaigns),
            'cycle_count': self.cycle_count
        })
        
        # 8. Cleanup old snapshots (every 10 cycles)
        if self.cycle_count % 10 == 0:
            self.snapshots.cleanup_old_snapshots(campaigns)
        
        elapsed = time.time() - cycle_start
        log.info(f'Cycle #{self.cycle_count} completed in {elapsed:.1f}s')
    
    def _fetch_ads_data(self, campaigns):
        """Fetch ads data from Shopee API and update campaigns in Firebase"""
        # Get a cookie to use (from first available channel)
        cookie_str = None
        for cam_id, cam in campaigns.items():
            cookie_str = self._get_cookie_for_channel(cam.get('channel', ''))
            if cookie_str:
                break
        
        if not cookie_str:
            log.warning('No valid cookie found for API calls')
            return
        
        # Fetch balance
        balance = self.api.get_ads_balance(cookie_str)
        if balance is not None:
            log.info(f'Ads balance: {balance}')
        
        # Fetch campaign list
        api_campaigns = self.api.get_ads_campaigns(cookie_str)
        if api_campaigns:
            log.info(f'API returned {len(api_campaigns)} campaigns')
            self._update_campaigns_from_api(campaigns, api_campaigns)
    
    def _update_campaigns_from_api(self, fb_campaigns, api_campaigns):
        """Update Firebase campaigns with fresh API data"""
        for api_cam in api_campaigns:
            # Try to match by channel name or campaign ID
            # (structure depends on actual API response)
            cam_name = api_cam.get('channelName', api_cam.get('username', ''))
            
            for fb_id, fb_cam in fb_campaigns.items():
                if fb_cam.get('channel', '').lower() == cam_name.lower():
                    updates = {
                        'spent_today': float(api_cam.get('cost', api_cam.get('spend', 0))),
                        'roas': float(api_cam.get('roas', 0)),
                        'ad_credit': float(api_cam.get('balance', api_cam.get('credit', 0))),
                        'visits': int(api_cam.get('visits', api_cam.get('impressions', 0))),
                        'conversion_rate': float(api_cam.get('conversionRate', 0)),
                        'last_update': datetime.now().isoformat()
                    }
                    
                    # Check if budget is full
                    budget = float(fb_cam.get('daily_budget', 200))
                    spent = updates['spent_today']
                    if spent >= budget * 0.99:
                        updates['status'] = 'budget_full'
                    
                    self.fb.update(f'shopee_ads/campaigns/{fb_id}', updates)
                    break
    
    def _merge_live_data(self, campaigns, live_data):
        """Merge clicks/cart/orders/sales from live monitor into campaigns"""
        for cam_id, cam in campaigns.items():
            channel = cam.get('channel', '')
            if not channel:
                continue
            
            name = channel.lower()
            for k, v in live_data.items():
                if isinstance(v, dict) and v.get('channel', '').lower() == name:
                    # Update with live data
                    self.fb.update(f'shopee_ads/campaigns/{cam_id}', {
                        'clicks': int(v.get('clicks', 0)),
                        'cart': int(v.get('added_to_cart', v.get('cart_count', 0))),
                        'orders': int(v.get('orders', 0)),
                        'sales': float(v.get('sales', 0)),
                    })
                    break
    
    def _get_cookie_for_channel(self, channel_name):
        """Get cookie for a channel from data_dict"""
        if channel_name and channel_name in self.data_dict:
            return self.data_dict[channel_name].get('cookie', '')
        # Try case-insensitive
        name = channel_name.lower() if channel_name else ''
        for k, v in self.data_dict.items():
            if k.lower() == name:
                return v.get('cookie', '')
        return ''


# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    monitor = AdsMonitor()
    monitor.start()
