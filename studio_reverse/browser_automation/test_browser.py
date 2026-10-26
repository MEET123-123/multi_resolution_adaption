import unittest
from pathlib import Path
import sys
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
from server import allowed_url, scrub, Browser

class BrowserTests(unittest.TestCase):
    def test_allowed_urls(self):
        self.assertTrue(allowed_url('https://themestudio.cloud.huawei.com.cn/#/project/1'))
        self.assertFalse(allowed_url('file:///C:/secret'))
        self.assertFalse(allowed_url('javascript:alert(1)'))
        self.assertFalse(allowed_url('https://user:password@example.com'))
    def test_network_redaction(self):
        self.assertEqual(scrub('https://example.com/api?token=secret#abc'),'https://example.com/api')
    def test_headless_browser_interaction(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(user_data_dir='', channel='msedge', headless=True)
            try:
                page=context.pages[0] if context.pages else context.new_page()
                page.set_content('<html><title>Demo</title><button onclick="this.innerText=\'已点击\'">新建作品</button><input placeholder="作品名"></html>')
                browser=object.__new__(Browser)
                browser.context=context
                browser.page=page
                browser.events=[]
                self.assertTrue(browser.action('click',{'by':'role','role':'button','query':'新建作品'})['ok'])
                self.assertEqual(page.get_by_role('button').inner_text(),'已点击')
                browser.action('fill',{'by':'placeholder','query':'作品名','value':'测试主题'})
                self.assertEqual(page.get_by_placeholder('作品名').input_value(),'测试主题')
                self.assertGreater(len(browser.action('screenshot',{})['image']),100)
            finally:
                context.close()
if __name__=='__main__': unittest.main()
