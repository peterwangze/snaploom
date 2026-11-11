"""Exercise the cloud OCR adapters with local response fixtures."""

from base64 import b64decode
import json
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage

from snaploom.cloud_ocr import CloudOCRError, recognize_cloud


class CloudOCRTests(TestCase):
    def setUp(self):
        self.image = QImage(8, 8, QImage.Format.Format_ARGB32)
        self.image.fill(QColor(200, 20, 40, 0))
        self.image.setText("Comment", "metadata that must stay local")

    @patch.dict("os.environ", {"SNAPLOOM_OCR_SPACE_KEY": "fixture-key"}, clear=True)
    @patch("snaploom.cloud_ocr._send_json")
    def test_ocr_space_post_contains_only_rendered_image_and_api_parameters(self, send):
        send.return_value = {
            "OCRExitCode": 1,
            "IsErroredOnProcessing": False,
            "ParsedResults": [{"ParsedText": "示例\r\ntext\r\n"}],
        }
        self.assertEqual(recognize_cloud(self.image, "ocr-space"), "示例\r\ntext")
        request, provider = send.call_args.args
        self.assertEqual(provider, "OCR.space")
        self.assertEqual(request.full_url, "https://api.ocr.space/parse/image")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Apikey"), "fixture-key")
        self.assertIn(b'name="OCREngine"\r\n\r\n2', request.data)
        self.assertIn(b'name="language"\r\n\r\nauto', request.data)
        self.assertNotIn(b"fixture-key", request.data)
        self.assertNotIn(b"metadata that must stay local", request.data)

    @patch.dict(
        "os.environ",
        {
            "TENCENTCLOUD_SECRET_ID": "fixture-id",
            "TENCENTCLOUD_SECRET_KEY": "fixture-secret",
            "TENCENTCLOUD_REGION": "ap-guangzhou",
        },
        clear=True,
    )
    @patch("snaploom.cloud_ocr._send_json")
    def test_tencent_post_sends_signed_headers_and_redacts_image_metadata(self, send):
        send.return_value = {"Response": {"TextDetections": [{"DetectedText": "第一行"}, {"DetectedText": "Second"}]}}
        self.assertEqual(recognize_cloud(self.image, "tencent"), "第一行\nSecond")
        request, _provider = send.call_args.args
        self.assertEqual(request.full_url, "https://ocr.tencentcloudapi.com/")
        self.assertEqual(request.get_header("X-tc-action"), "GeneralBasicOCR")
        self.assertEqual(request.get_header("X-tc-version"), "2018-11-19")
        self.assertEqual(request.get_header("X-tc-region"), "ap-guangzhou")
        authorization = request.get_header("Authorization")
        self.assertRegex(
            authorization,
            r"^TC3-HMAC-SHA256 Credential=fixture-id/\d{4}-\d{2}-\d{2}/ocr/tc3_request, "
            r"SignedHeaders=content-type;host, Signature=[0-9a-f]{64}$",
        )
        self.assertNotIn("fixture-secret", authorization)
        body = json.loads(request.data)
        self.assertEqual(body["LanguageType"], "auto")
        uploaded = QImage.fromData(b64decode(body["ImageBase64"]))
        self.assertEqual(uploaded.size(), self.image.size())
        self.assertEqual(uploaded.pixelColor(0, 0), QColor("white"))
        self.assertEqual(uploaded.textKeys(), [])

    @patch.dict("os.environ", {}, clear=True)
    @patch("snaploom.cloud_ocr._send_json")
    def test_missing_credentials_stop_before_any_request(self, send):
        with self.assertRaisesRegex(CloudOCRError, "SNAPLOOM_OCR_SPACE_KEY"):
            recognize_cloud(self.image, "ocr-space")
        send.assert_not_called()

    @patch.dict("os.environ", {"SNAPLOOM_OCR_SPACE_KEY": "fixture-key"}, clear=True)
    @patch("snaploom.cloud_ocr._send_json")
    def test_ocr_space_failure_is_actionable_without_exposing_response(self, send):
        send.return_value = {"OCRExitCode": 4, "IsErroredOnProcessing": True, "ErrorMessage": "private detail"}
        with self.assertRaises(CloudOCRError) as error:
            recognize_cloud(self.image, "ocr-space")
        self.assertIn("OCR.space", str(error.exception))
        self.assertNotIn("private detail", str(error.exception))
