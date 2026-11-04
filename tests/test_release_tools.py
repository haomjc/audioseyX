import os
import unittest
from unittest.mock import patch, MagicMock

from tools.readthedocs import api, NoRedirect
from tools.audit_distribution import check_names


class MaintenanceClientTests(unittest.TestCase):
    def test_remote_write_requires_explicit_local_api_credential(self):
        with patch.dict(os.environ,{"READTHEDOCS_TOKEN":""}),patch("tools.readthedocs.build_opener") as opener:
            with self.assertRaisesRegex(ValueError,"Set READTHEDOCS_TOKEN"):
                api("POST","projects/")
            opener.assert_not_called()

    def test_invalid_header_token_is_rejected_without_echoing_the_secret(self):
        fake="not-a-real-token\nsecond-line"
        with patch.dict(os.environ,{"READTHEDOCS_TOKEN":fake}),patch("tools.readthedocs.build_opener") as opener:
            with self.assertRaises(ValueError) as caught:api("GET","projects/audioseyx/")
            self.assertNotIn("not-a-real-token",str(caught.exception))
            opener.assert_not_called()

    def test_token_is_sent_only_as_an_authorization_header(self):
        response=MagicMock();response.read.return_value=b'{"slug":"audioseyx"}'
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch.dict(os.environ,{"READTHEDOCS_TOKEN":"not-a-real-token"}),patch("tools.readthedocs.build_opener",return_value=opener):
            result=api("GET","projects/audioseyx/")
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,"https://app.readthedocs.org/api/v3/projects/audioseyx/")
        self.assertNotIn("not-a-real-token",request.full_url)
        self.assertEqual(request.get_header("Authorization"),"Token not-a-real-token")
        self.assertEqual(result["slug"],"audioseyx")
        self.assertIsNone(NoRedirect().redirect_request(request,None,302,"Found",{},"https://example.com"))

    def test_user_native_synthesis_files_are_rejected_from_public_distributions(self):
        for name in ["audioseyx/main.npz","project/.native-profiles/manifest.json","audioseyx/matrix.npy"]:
            with self.assertRaises(ValueError):check_names([name])


if __name__=="__main__":unittest.main()
