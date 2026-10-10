"""Run: python -m unittest discover tests

Offline checks for the price-trend lookup and the listing parser.
"""
import unittest
from datetime import date, timedelta

from test_settings import load

m = load()


def _trends(entries, today, price="1.000.000"):
    history = {"https://shop.example/item": entries}
    item = {"name": "Item", "price": price, "product_url": "https://shop.example/item"}
    m.update_history_and_get_trends(history, "shop", "ram", [item], today)
    return item["trend"]


def _days(today, *days_ago, price=500000):
    return [{"date": (today - timedelta(days=d)).isoformat(), "price": price} for d in sorted(days_ago, reverse=True)]


class Trends(unittest.TestCase):
    def test_gap_in_history_gives_no_trend(self):
        # Same shape as tech-price-state on 2026-10-10: daily points up to
        # 08-20, nothing until 10-07. Both "7 ngày" and "1 tháng" used to
        # fall back to 08-20 and show the same figure.
        today = date(2026, 10, 10)
        entries = []
        d = date(2026, 7, 21)
        while d <= date(2026, 8, 20):
            entries.append({"date": d.isoformat(), "price": 500000})
            d += timedelta(days=1)
        for day in (7, 8, 9):
            entries.append({"date": f"2026-10-0{day}", "price": 900000})
        trend = _trends(entries, today)
        self.assertNotIn("7 ngày", trend)
        self.assertNotIn("1 tháng", trend)
        self.assertNotIn("6 tháng", trend)
        self.assertNotIn("1 năm", trend)

    def test_uses_closest_point_within_tolerance(self):
        today = date(2026, 10, 10)
        entries = _days(today, 9, price=500000) + _days(today, 6, price=800000)
        # 6 days ago is closer to the 7-day target than 9 days ago.
        self.assertEqual(_trends(entries, today)["7 ngày"], 25.0)

    def test_tie_prefers_the_earlier_point(self):
        today = date(2026, 10, 10)
        entries = _days(today, 9, price=500000) + _days(today, 5, price=800000)
        self.assertEqual(_trends(entries, today)["7 ngày"], 100.0)

    def test_point_outside_tolerance_is_ignored(self):
        today = date(2026, 10, 10)
        self.assertEqual(_trends(_days(today, 10), today)["7 ngày"], 100.0)
        self.assertNotIn("7 ngày", _trends(_days(today, 11), today))
        self.assertNotIn("7 ngày", _trends(_days(today, 3), today))

    def test_each_window_uses_its_own_point(self):
        today = date(2026, 10, 10)
        entries = (
            _days(today, 365, price=250000) + _days(today, 180, price=400000)
            + _days(today, 30, price=500000) + _days(today, 7, price=800000)
        )
        self.assertEqual(
            _trends(entries, today),
            {"7 ngày": 25.0, "1 tháng": 100.0, "6 tháng": 150.0, "1 năm": 300.0},
        )

    def test_windows_never_overlap(self):
        ranges = sorted(
            (days - m.TREND_MATCH_TOLERANCE_DAYS[label], days + m.TREND_MATCH_TOLERANCE_DAYS[label])
            for label, days in m.TREND_WINDOWS
        )
        self.assertGreater(ranges[0][0], 0)
        for (_, hi), (lo, _) in zip(ranges, ranges[1:]):
            self.assertLess(hi, lo)
        self.assertLessEqual(ranges[-1][1], m.HISTORY_MAX_AGE_DAYS)


def _page(*cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


class ParseListing(unittest.TestCase):
    def test_spec_bullet_is_not_a_product(self):
        # An Phát-style card: name link, spec bullets, then the price. The
        # last bullet used to be listed as a "Laptop" named "Ổ cứng: ...".
        html = _page(
            "<div><a href='/laptop-dell-inspiron-3530.html'>Laptop Dell Inspiron 15 3530 i5-1334U 16GB 512GB</a>"
            "<ul><li>CPU: Intel Core i5-1334U</li><li>RAM: 16GB DDR4 3200MHz</li>"
            "<li>Ổ cứng: 512GB PCIe® 4.0 NVMe™ M.2 SSD</li></ul><span>83.990.000 ₫</span></div>"
        )
        items = m.parse_listing(html, base_url="https://www.anphatpc.com.vn/laptop.html")
        self.assertEqual([(i["name"], i["price"]) for i in items],
                         [("Laptop Dell Inspiron 15 3530 i5-1334U 16GB 512GB", "83.990.000")])
        self.assertEqual(items[0]["product_url"], "https://www.anphatpc.com.vn/laptop-dell-inspiron-3530.html")

    def test_spec_label_names_rejected(self):
        for name in (
            "Ổ cứng: 512GB PCIe® 4.0 NVMe™ M.2 SSD",
            "Kích thước : 3.5 inch xxxxxxxxxx",
            "- Kích thước: 3.5Inch xxxxxxxxxxx",
            "Kết nối : SATA 3 6.0Gb/s xxxxxx",
            "Phân giải điểm ảnh: FHD (1920x1080)",
            "Mật độ phân giải: 4K - UHD - 3840 x 2160",
            "Màn hình: 15.6 inch FHD IPS 144Hz",
            "Card đồ họa: NVIDIA RTX 4050 6GB",
            "VGA: NVIDIA GeForce RTX 4060 8GB",
            "Pin: 4 Cell 70Wh xxxxxxxxxxxx",
        ):
            with self.subTest(name=name):
                html = _page(f"<div><a href='/p'>{name}</a><span>1.990.000 ₫</span></div>")
                self.assertEqual(m.parse_listing(html, base_url="https://shop.example/"), [])
                self.assertTrue(m.SPEC_LABEL_RE.match(name))

    def test_line_without_product_link_is_not_a_product(self):
        html = _page(
            "<div><a href='/gia-treo-man-hinh'><img alt='' src='/a.jpg'></a>"
            "<p>Chịu được tới 45.5kgs / TV màn hình phẳng (32 - 75 Inches)</p><span>1.250.000 ₫</span></div>",
            "<div><a href='/man-hinh-lg-27'>Màn hình LG 27GP850-B 27 inch 2K Nano IPS 180Hz</a>"
            "<span>8.990.000 ₫</span></div>",
        )
        items = m.parse_listing(html, base_url="https://shop.example/")
        self.assertEqual([i["name"] for i in items], ["Màn hình LG 27GP850-B 27 inch 2K Nano IPS 180Hz"])

    def test_real_names_starting_with_label_words_kept(self):
        names = [
            "RAM Laptop Acer SD100 8GB DDR4 3200MHz 1.2v SD100-8GB-3200-1R8",
            "Màn hình MSI PRO MP225 E12VL VA 21.45 inch FHD 120Hz",
            "Card màn hình Gigabyte RTX 5070 WINDFORCE OC SFF 12G GDDR7 (GV-N5070WF3OC-12GD)",
            "Ổ cứng HDD WD Ultrastar DC HA340 8TB 3.5 inch, 7200RPM, SATA, 256MB Cache (WUS721208BLE6L4)",
            "VGA ARKTEK GTX 1660 Super 6GB GDDR6",
            "Laptop Lenovo V14 G5 IRL 83HD0062VA (i5-13420H, Intel Graphics, RAM 16GB DDR5, SSD 512GB, 14 Inch IPS FHD 60Hz, NoOS)",
        ]
        html = _page(*(
            f"<div><a href='/p{n}'><img alt='{name}' src='/i{n}.jpg'></a><span>{n + 1}.000.000 ₫</span></div>"
            for n, name in enumerate(names)
        ))
        self.assertEqual([i["name"] for i in m.parse_listing(html, base_url="https://shop.example/")], names)


if __name__ == "__main__":
    unittest.main()
