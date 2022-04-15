from django.urls import path
from crs.views import CSVDownloadView

urlpatterns = [
    path('csv-download/', CSVDownloadView.as_view(), name='csv-download'),
]
