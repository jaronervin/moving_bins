from django.urls import path

from . import booking_flow, ops, payment_views, renter_views, totes, views

urlpatterns = [
    path("", views.home, name="home"),
    path("book/", booking_flow.package, name="book_package"),
    path("book/extras/", booking_flow.extras, name="book_extras"),
    path("book/addresses/", booking_flow.addresses, name="book_addresses"),
    path("book/delivery/", booking_flow.delivery, name="book_delivery"),
    path("book/contact/", booking_flow.contact, name="book_contact"),
    path("book/review/", booking_flow.review, name="book_review"),
    path("book/start-over/", booking_flow.start_over, name="book_start_over"),
    path("check-area/", booking_flow.check_area, name="check_area"),
    path("b/<str:token>/", views.booking_detail, name="booking_detail"),
    path("b/<str:token>/card/", payment_views.card, name="card"),
    path("b/<str:token>/card/session", payment_views.card_session, name="card_session"),
    path("b/<str:token>/card/done", payment_views.card_done, name="card_done"),
    path("b/<str:token>/sign/", payment_views.resign, name="resign"),
    path("b/<str:token>/cancel/", renter_views.cancel, name="renter_cancel"),
    path("b/<str:token>/change/", renter_views.change, name="renter_change"),
    path("b/<str:token>/add-week/", renter_views.add_week, name="renter_add_week"),
    path("b/<str:token>/agreement/", views.signed_agreement, name="signed_agreement"),
    path("agreement/", views.agreement, name="agreement"),
    path("stripe/webhook/", payment_views.stripe_webhook, name="stripe_webhook"),
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
