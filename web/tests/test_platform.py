import unittest
from web.platform import Platform,cards,checked

class AdapterTests(unittest.TestCase):
    def test_nested_search_cards_and_html(self):
        items=cards([{'card_group':[{'mblog':{'id':12345678,'text':'你好<br><b>微博</b>','user':{'id':7,'screen_name':'作者'}}}]}])
        self.assertEqual(items[0]['id'],'12345678');self.assertNotIn('<',items[0]['text']);self.assertEqual(items[0]['uid'],'7')
    def test_resolve_disallows_arbitrary_network(self):
        self.assertEqual(Platform.resolve('https://m.weibo.cn/detail/123456789'),'123456789')
        with self.assertRaises(ValueError):Platform.resolve('https://127.0.0.1/detail/123456789')
        with self.assertRaises(ValueError):checked((True,'ok',{'ok':0}))
