from django.urls import path

from . import booking_flow, ops, totes, views

urlpatterns = [
    path("", views.home, name="home"),
    path("book/", booking_flow.dates, name="book_dates"),
    path("book/addresses/", booking_flow.addresses, name="book_addresses"),
    path("book/times/", booking_flow.times, name="book_times"),
    path("book/contact/", booking_flow.contact, name="book_contact"),
    path("book/review/", booking_flow.review, name="book_review"),
    path("book/start-over/", booking_flow.start_over, name="book_start_over"),
    path("b/<str:token>/", views.booking_detail, name="booking_detail"),
    path("my-booking/", views.find_booking, name="find_booking"),
    # Tote labels. Codes in QR labels are upper case (smaller QR codes), so both work.
    path("q/<str:code>", totes.tote, name="tote"),
    path("Q/<str:code>", totes.tote),
    path("tote/", totes.find_tote, name="find_tote"),
    # Owner tools (staff only)
    path("ops/", ops.index, name="ops_index"),
    path("ops/bin/<str:code>/", ops.bin_detail, name="ops_bin"),
    path("ops/b/<str:reference>/<str:mode>/", ops.scanner, name="ops_scanner"),
    path("ops/check-in/", ops.check_in, name="ops_check_in"),
    path("ops/b/<str:reference>/finish-check-in", ops.finish_check_in_view, name="ops_finish_check_in"),
    path("ops/api/scan", ops.scan_api, name="ops_scan_api"),
    path("ops/b/<str:reference>/decline", ops.decline, name="ops_decline"),
    path("ops/labels/", ops.labels, name="ops_labels"),
]
