import tempfile
import unittest
from unittest.mock import patch
from web.platform import Platform,PlatformError

class ProfileTests(unittest.TestCase):
    def test_profile_fields_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            platform=Platform(directory);platform.cookie=lambda:'synthetic'
            with patch.object(platform.web,'getUserInfo',return_value=(True,'',{'ok':1,'data':{'user':{'id':12,'screen_name':'博主','description':'简介','followers_count':100}}})):
                item=platform.tool('profile',{'uid':'12'})['items'][0]
                self.assertEqual(item['kind'],'profile');self.assertIn('100',item['text'])
            with patch.object(platform.web,'getUserInfo',return_value=(False,'',None)):
                with self.assertRaises(PlatformError):platform.tool('profile',{'uid':'12'})
            with self.assertRaises(PlatformError):platform.tool('profile',{'uid':'bad'})
