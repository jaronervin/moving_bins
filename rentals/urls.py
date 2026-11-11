from django.urls import path

from . import booking_flow, views

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
]
