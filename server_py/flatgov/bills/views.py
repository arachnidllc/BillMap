import json

from functools import reduce
from typing import Dict
from operator import itemgetter

from django.conf import settings
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import TemplateView, DetailView
from django.views.generic.edit import FormMixin

from bills.models import (
    Bill,
    CboReport,
    cleanReasons,
    CommitteeDocument,
    Cosponsor,
    Statement,
)
from bills.serializers import RelatedBillSerializer
from bills.utils import sort_bills_for_congress
from common.elastic_load import getSimilarSections, moreLikeThis, getResultBillnumbers
from common.tasks import send_email
from crs.models import CrsReport
from feedback.forms import FeedbackModelForm


def deep_get(dictionary: Dict, *keys):
    """
    A Dict utility to get a field; returns None if the field does not exist
    Args:
      dictionary (Dict): an arbitrary dictionary

    Returns:
      any: value of the specified key, or None if the field does not exist
    """

    return reduce(
        lambda d, key: d.get(key, None)
        if isinstance(d, dict) else None, keys, dictionary)


CONGRESS_DATA_PATH = getattr(settings, "CONGRESS_DATA_PATH", None)
BILLS_META_JSON_PATH = getattr(settings, "BILLS_META_JSON_PATH", None)
RELATED_BILLS_JSON_PATH = getattr(settings, "RELATED_BILLS_JSON_PATH", None)
TITLES_INDEX_JSON_PATH = getattr(settings, "TITLES_INDEX_JSON_PATH", None)
SIMILARITY_THRESHOLD = .1
IDENTICAL_REASONS = ['identical', 'nearly identical', 'title match (main)']

BILL_REGEX = r'([1-9][0-9]{2})([a-z]+)(\d+)'


# Utilities. These should go in a utils.py module
def billIdToBillNumber(bill_id: str) -> str:
    """
    Converts a bill_id of the form `hr299-116` into `116hr299`
    Args:
        bill_id (str): hyphenated bill_id from bill status JSON
    Returns:
        str: billCongressTypeNumber (e.g. 116hr299)
    """
    # TODO test if it has the right form, otherwise throw an exception
    return ''.join(reversed(bill_id.split('-')))


def makeSponsorBracket(sponsor: dict, party='X') -> str:
    # TODO: in the future, make party required
    state = sponsor.get('state', '')
    if not state:
        state = ''
    district = sponsor.get('district', '')
    # Implemented this because default of '' does not seem to work
    if not district:
        district = ''

    return '[' + party + '-' + state + district + ']'


class BillListView(TemplateView):
    template_name = 'bills/list.html'

    def get_context_data(self, **kwargs):
        # from uscongress.tasks import bill_similarity_task
        # bill_similarity_task(26)
        context = super().get_context_data(**kwargs)
        return context


def similar_bills_view(request):
    noResults = False
    # after the redirect (in the views.py that handles your redirect)
    queryText = request.session.get('queryText')
    if not queryText:
        queryText = ''
    res = moreLikeThis(queryText=queryText)
    similarBillNumbers = getResultBillnumbers(res)
    similarSections = sorted(getSimilarSections(res),
                             key=itemgetter('score'),
                             reverse=True)
    bestMatch = {}
    if not similarSections or len(similarSections) == 0:
        noResults = True
    else:
        bestMatch = similarSections[0]

    context = {
        "billQuery": {
            "queryText": queryText,
            "bestMatch": bestMatch,
            "similarBillNumbers": similarBillNumbers,
            "similarSections": json.dumps(similarSections),
            "noResults": noResults
        }
    }
    return render(request, 'bills/bill-similar.html', context)


class BillDetailView(FormMixin, DetailView):
    model = Bill
    template_name = 'bills/detail.html'
    slug_field = 'bill_congress_type_number'
    identical_bill_numbers = []
    form_class = FeedbackModelForm

    def get_success_url(self):
        return reverse_lazy('bill-detail', kwargs={'slug': self.object.bill_congress_type_number})

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def form_valid(self, form):
        form.save()

        email_subject = 'Received a feedback'
        email_list = [settings.EMAIL_HOST_USER]

        url = self.request.scheme + "://" + self.request.META.get('HTTP_HOST') + form.instance.get_admin_url()
        email_content = f'<a href="{url}">Click here to see feedback</a>'
        send_email.delay(email_subject, email_list, email_content)
        return super().form_valid(form)

    # paginate_by = settings.DJANGO_TABLES2_PAGINATE_BY

    def get_qs_related_bill(self):
        congress_list = self.object.get_related_bill_numbers()
        qs = Bill.objects.filter(bill_congress_type_number__in=congress_list)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['identical_bill_numbers'] = self.get_identical_bill_numbers()
        context['cosponsors_dict'] = self.get_cosponsors_dict()
        context['committees_dict'] = self.object.committees_dict
        context['committees_dict_deduped'] = self.get_committees_dict_deduped()
        context['committees_map'] = self.get_committees_map()
        context['statements'] = self.get_related_statements()
        context['committees'] = self.get_related_committees()
        context['crs_reports'] = self.get_crs_reports()
        context['cbo_reports'] = self.get_related_cbo()
        context['related_bills'] = self.get_related_bills()
        context['similar_bills'] = self.object.get_similar_bills
        context['es_similarity'] = self.object.es_similarity
        context['current_bill_score'] = self.get_current_bill_score()
        context['cosponsors_for_bills'] = self.get_cosponsors_for_same_bills()
        context['propublica_api_key'] = settings.PROPUBLICA_CONGRESS_API_KEY
        context['no_data_message'] = "No data available for this table"
        context['feedback_form'] = self.get_form()

        return context

    def get_related_statements(self, **kwargs):
        slug = self.kwargs['slug']
        bill_numbers = []
        bill_numbers.append(slug[3:])
        [bill_numbers.append(number[3:]) for number in self.identical_bill_numbers if number[3:] not in bill_numbers]
        q_list = map(lambda n: Q(bill_number__iexact=n), bill_numbers)
        q_list = reduce(lambda a, b: a | b, q_list)
        return Statement.objects.filter(q_list).filter(
            congress__iexact=slug[:3])

    def get_related_committees(self, **kwargs):
        slug = self.kwargs['slug']
        bill_numbers = []
        bill_numbers.append(slug[3:])
        [bill_numbers.append(number[3:]) for number in self.identical_bill_numbers if number[3:] not in bill_numbers]
        q_list = map(lambda n: Q(bill_number__iexact=n), bill_numbers)
        q_list = reduce(lambda a, b: a | b, q_list)
        return CommitteeDocument.objects.filter(q_list).filter(congress__iexact=slug[:3])

    def get_crs_reports(self, **kwargs):
        slug = self.kwargs['slug']
        bill_numbers = []
        bill_numbers.append(slug)
        [bill_numbers.append(number) for number in self.identical_bill_numbers if number[3:] not in bill_numbers]
        q_list = map(lambda n: Q(bills__bill_congress_type_number__iexact=n), bill_numbers)
        q_list = reduce(lambda a, b: a | b, q_list)
        crs_reports = list(CrsReport.objects.filter(q_list).distinct('title'))

        crs_reports_context = []
        for report in crs_reports:
            pdf_everycrs = ""
            html_everycrs = ""
            main_everycrs = ""
            if report.file:
                pdf_everycrs = "https://www.everycrsreport.com/files/" + report.file
                fileparts = report.file.split("_")
                if len(fileparts) == 3:
                    main_everycrs = "https://www.everycrsreport.com/reports/" + fileparts[1] + ".html"
            if report.html_url:
                html_everycrs = "https://www.everycrsreport.com/files/" + report.html_url
            crs_bill_numbers = report.bills.all().values_list('bill_congress_type_number', flat=True)
            identical_bill_number = list(set(bill_numbers).intersection(crs_bill_numbers))

            context_item = {
                "title": report.title,
                "identical_bill_number": identical_bill_number,
                "identical_bill_numbers": crs_bill_numbers,
                "date": report.date,
                "main_everycrs": main_everycrs,
                "html_everycrs": html_everycrs,
                "pdf_everycrs": pdf_everycrs
            }
            metadata = json.loads(report.metadata)
            versions = metadata.get('versions', [])
            if versions and len(versions) > 0:
                context_item["link"] = versions[0].get('sourceLink', '')
            crs_reports_context.append(context_item)
        return crs_reports_context

    def get_related_cbo(self, **kwargs):
        slug = self.kwargs['slug']
        bill_numbers = []
        bill_numbers.append(slug[3:])
        [bill_numbers.append(number[3:]) for number in self.identical_bill_numbers if number[3:] not in bill_numbers]
        q_list = map(lambda n: Q(bill_number__iexact=n), bill_numbers)
        q_list = reduce(lambda a, b: a | b, q_list)
        return CboReport.objects.filter(q_list).filter(
            congress__iexact=slug[:3])

    def get_related_bills(self):
        qs = self.get_qs_related_bill()
        serializer = RelatedBillSerializer(qs,
                                           many=True,
                                           context={'bill': self.object})
        return serializer.data

    def get_committees_dict_deduped(self, **kwargs):
        committees_dict = self.object.committees_dict
        if not committees_dict:
            return []
        deduped = []
        # list of committees
        seen = []
        for committeeItem in committees_dict:
            k = committeeItem.get('committee', '')
            if not k or k in seen:
                continue

            seen.append(k)
            deduped.append(committeeItem)
        return deduped

    def get_committees_map(self, **kwargs):
        deduped = self.get_committees_dict_deduped()
        return {
            committee.get('committee_id'): committee.get('committee')
            for committee in deduped
        }

    def get_cosponsors_dict(self):
        cosponsors = self.object.cosponsors_dict
        cosponsors = sorted(cosponsors, key=lambda i: i.get('name'))

        sponsor = self.object.sponsor
        sponsor['sponsor'] = True
        sponsor_name = sponsor.get('name', '')
        if sponsor_name:
            sponsors = []
            try:
                sponsors = list(filter(lambda cosponsor: cosponsor.get('name', '') == sponsor_name, cosponsors))
            except Exception:
                pass
            if sponsors:
                cosponsors.remove(sponsors[0])
                cosponsors.insert(0, sponsors[0])
            else:
                cosponsors.insert(0, sponsor)
        # Add party from Cosponsors table
        unoriginal_cosponsors = []
        sorted_unoriginal_ranked_cosponsors = []
        unoriginal_unranked_cosponsors = []
        original_cosponsors = []
        sorted_original_ranked_cosponsors = []  #
        original_unranked_cosponsors = []  #
        for i, cosponsor in enumerate(cosponsors):
            bioguide_id = cosponsor.get("bioguide_id", "")
            committee_id = cosponsor.get('committee_id')
            if bioguide_id:
                try:
                    cosponsor_item = Cosponsor.objects.get(bioguide_id=bioguide_id)
                    if cosponsor_item:
                        cosponsor["current"] = True
                    else:
                        cosponsor["current"] = False

                except Exception:
                    cosponsor["current"] = False
                    continue
                cosponsor['party'] = cosponsor_item.party
                cosponsor['name_full_official'] = cosponsor_item.name_full_official
                for committee in cosponsor_item.committees:

                    if bioguide_id == committee.get('bioguide'):
                        committee_id = committee.get('committee')
                        for committee_dict in self.object.committees_dict:
                            if committee_id == committee_dict.get('committee_id'):
                                cosponsor['committee_id'] = committee_id
                                cosponsor['rank'] = committee.get('rank')
                                cosponsor['committee_name'] = committee_dict.get('committee')

        for cosponsor in cosponsors[1:]:
            if cosponsor.get('original_cosponsor'):
                original_cosponsors.append(cosponsor)
            else:
                unoriginal_cosponsors.append(cosponsor)

        for original_cosponsor in original_cosponsors:
            if type(original_cosponsor.get('rank')) == int:
                sorted_original_ranked_cosponsors.append(original_cosponsor)
            else:
                original_unranked_cosponsors.append(original_cosponsor)

        for unoriginal_cosponsor in unoriginal_cosponsors:
            if type(unoriginal_cosponsor.get('rank')) == int:
                sorted_unoriginal_ranked_cosponsors.append(unoriginal_cosponsor)
            else:
                unoriginal_unranked_cosponsors.append(unoriginal_cosponsor)

        sorted_original_ranked_cosponsors = sorted(sorted_original_ranked_cosponsors, key=lambda i: i.get('rank'))
        sorted_unoriginal_ranked_cosponsors = sorted(sorted_unoriginal_ranked_cosponsors, key=lambda i: i.get('rank'))

        return cosponsors[
               :1] + sorted_original_ranked_cosponsors + original_unranked_cosponsors + sorted_unoriginal_ranked_cosponsors + unoriginal_unranked_cosponsors

    def get_billnumbers_similar(self):
        return [bill.get('bill_congress_type_number', '')
                for bill in self.object.get_similar_bills]

    def get_current_bill_score(self):
        if self.object.bill_congress_type_number in self.get_billnumbers_similar():
            current_bill = next(
                filter(
                    lambda bill: bill.get('bill_congress_type_number') == self.
                        object.bill_congress_type_number,
                    self.object.get_similar_bills))
            current_bill_score = current_bill.get('score')
        else:
            current_bill_score = 0
        return current_bill_score

    #  Get identical or nearly identical bills with the following, or equivalent
    def get_identical_bill_numbers(self):
        current_bill_score = self.get_current_bill_score()

        identical_bill_numbers = [billnumber for billnumber in [bill.get('bill_congress_type_number', '')
            for bill in self.object.get_similar_bills
            if (any(x in cleanReasons(bill.get('reason').split(", ")) for x in IDENTICAL_REASONS)
            or (current_bill_score > 0 and (abs(bill.get('score') - current_bill_score) / current_bill_score < SIMILARITY_THRESHOLD)))]
            if billnumber]
        self.identical_bill_numbers = identical_bill_numbers
        sort_bills_for_congress(identical_bill_numbers)
        return identical_bill_numbers

    # SIMILARITY_THRESHOLD: Fraction difference in score that will still be considered identical
    def get_cosponsors_for_same_bills(self):
        committees_map = self.get_committees_map()
        billnumbers = self.identical_bill_numbers
        if self.object.bill_congress_type_number not in billnumbers:
            billnumbers = [self.object.bill_congress_type_number, *billnumbers]
        billids = [Bill.objects.get(
            bill_congress_type_number=billnumber).id for billnumber in billnumbers]
        bill_dict = {billid: billnumbers[i] for i, billid in enumerate(billids)}
        cosponsors_for_bills = Cosponsor.objects.filter(
            bill__in=billids).values("bioguide_id", "name_full_official",
                                     "party", "state", "leadership",
                                     "committees", "bill")

        # cosponsors_deduped is a dict, with bioguide_id as key
        # It deduplicates by bioguide_id and combine billnumbers
        cosponsors_deduped = {}
        for cosponsor in cosponsors_for_bills:
            bctn = bill_dict.get(cosponsor.get('bill', ''), '')
            bioguide_id = cosponsor.get('bioguide_id')
            if cosponsors_deduped.get(bioguide_id):
                cosponsors_deduped[bioguide_id][
                    'bill_congress_type_numbers'].append(bctn)
            else:
                cosponsor['bill_congress_type_numbers'] = [bctn]
                committees_relevant = [
                    committeeItem
                    for committeeItem in cosponsor.get('committees', [])
                    if committeeItem.get('committee') in committees_map.keys()
                ]
                cosponsor['committees_named'] = [
                    committees_map.get(committeeItem.get('committee'), '')
                    for committeeItem in committees_relevant
                ]
                cosponsor['ranks'] = [
                    committeeItem.get('rank', '')
                    for committeeItem in committees_relevant
                ]
                cosponsors_deduped[bioguide_id] = cosponsor

        return list(cosponsors_deduped.values())


class BillToBillView(DetailView):
    model = Bill
    template_name = 'bills/compare.html'
    slug_field = 'bill_congress_type_number'

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(**kwargs)
        second_bill = self.kwargs.get('second_bill')
        context['second_bill'] = Bill.objects.get(
            bill_congress_type_number=second_bill)
        context['bill_to_bill'] = self.object.es_similar_bills_dict.get(
            second_bill, [])
        return context
