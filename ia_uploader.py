sam@LX15PRO:~/storyGraphData$ cat ia_uploader.py
import os
import sys
import logging
import traceback

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dateutil.relativedelta import relativedelta
from internetarchive import upload

SRC_BASE = './tmp'
MAX_WORKERS = 10

logger = logging.getLogger(__name__)


def generic_error_info(slug=''):
    exc_type, exc_obj, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]  # type: ignore
    err_msg = fname + ', ' + str(exc_tb.tb_lineno) + ', ' + str(sys.exc_info())  # type: ignore
    logger.error(err_msg + slug)
    return err_msg


def upload_helper(item_identifier, dst_file, src_file, upload_metadata, **kwargs):
    ia_no_upload = kwargs.get('ia_no_upload', False)

    if ia_no_upload is True:
        print(f'  [no_upload] {item_identifier}/{dst_file}')
        return

    if not os.path.exists(src_file):
        print(f'  SKIP missing: {src_file}')
        return

    try:
        upload(
            item_identifier,
            files={dst_file: src_file},
            metadata=upload_metadata,
            retries=5,             # retry transient errors up to 5 times
            retries_sleep=10,      # wait 10s between retries
        )
        print(f'  OK  {item_identifier}/{dst_file}')
    except Exception as e:
        print(f'  FAIL {item_identifier}/{dst_file} - {type(e).__name__}: {e}')


def ia_upload(day_or_week_or_month_or_year, start_datetime):
    ia_col = 'storygraph'
    ia_upload_metadata = {
        'collection': ia_col,
        'uploader': 'Alexander C. Nwala <alexandernwala@gmail.com>',
        'mediatype': 'data'
    }

    if day_or_week_or_month_or_year is None or start_datetime == '':
        return

    o_start_datetime = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M:%S")

    if day_or_week_or_month_or_year == 'day':
        yyyy, mm, dd = start_datetime.split('T')[0].split('-')
        item_identifier = f'{ia_col}-data-usa-{yyyy}-{mm}'
        dst_file = f'{dd}/stories-day-{yyyy}-{mm}-{dd}.json.gz'
        src_file = f'{SRC_BASE}/days/{yyyy}-{mm}-{dd}.json.gz'
        upload_helper(item_identifier, dst_file, src_file, ia_upload_metadata, ia_no_upload=False)

    elif day_or_week_or_month_or_year == 'week':
        iso_year, iso_week, _ = o_start_datetime.isocalendar()
        monday = datetime.strptime(f'{iso_year}-W{iso_week}-1', "%G-W%V-%u")
        yyyy, mm, dd = monday.strftime("%Y-%m-%d").split('-')
        item_identifier = f'{ia_col}-data-usa-{yyyy}-{mm}'
        dst_file = f'{dd}/stories-week-{yyyy}-{mm}-w{iso_week:02}.json.gz'
        src_file = f'{SRC_BASE}/weeks/{yyyy}-{mm}-{dd}.json.gz'
        upload_helper(item_identifier, dst_file, src_file, ia_upload_metadata, ia_no_upload=False)

    elif day_or_week_or_month_or_year == 'month':
        yyyy, mm, _ = start_datetime.split('T')[0].split('-')
        item_identifier = f'{ia_col}-data-usa-{yyyy}-{mm}'
        dst_file = f'01/stories-month-{yyyy}-{mm}.json.gz'
        src_file = f'{SRC_BASE}/months/{yyyy}-{mm}-01.json.gz'
        upload_helper(item_identifier, dst_file, src_file, ia_upload_metadata, ia_no_upload=False)

    elif day_or_week_or_month_or_year == 'year':
        yyyy, _, _ = start_datetime.split('T')[0].split('-')
        item_identifier = f'{ia_col}-data-usa-{yyyy}-01'
        dst_file = f'01/stories-year-{o_start_datetime.year}.json.gz'
        src_file = f'{SRC_BASE}/years/{yyyy}-01-01.json.gz'
        upload_helper(item_identifier, dst_file, src_file, ia_upload_metadata, ia_no_upload=False)


def upload_date_range(interval, start_yyyy_mm_dd, end_yyyy_mm_dd, ia_no_upload=False, max_workers=MAX_WORKERS):
    """Upload all interval-aligned dates in [start, end] in parallel."""

    step_map = {
        'day': timedelta(days=1),
        'week': timedelta(weeks=1),
        'month': relativedelta(months=1),
        'year': relativedelta(years=1),
    }
    if interval not in step_map:
        raise ValueError(f"Invalid interval: {interval}. Must be one of {list(step_map.keys())}")

    current = datetime.strptime(start_yyyy_mm_dd, "%Y-%m-%d")
    end = datetime.strptime(end_yyyy_mm_dd, "%Y-%m-%d")

    # Normalize start so stepping aligns with interval boundaries
    if interval == 'week':
        # snap to Monday of that week
        current = current - timedelta(days=current.weekday())
    elif interval == 'month':
        current = current.replace(day=1)
    elif interval == 'year':
        current = current.replace(month=1, day=1)

    step = step_map[interval]

    # Build list of dates first
    dates = []
    while current <= end:
        dates.append(current.strftime("%Y-%m-%dT00:00:00"))
        current += step

    print(f'Queuing {len(dates)} {interval} uploads with {max_workers} workers')
    print(f'Range: {start_yyyy_mm_dd} to {end_yyyy_mm_dd}')
    print('-' * 60)

    n_done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(ia_upload, interval, d): d for d in dates}
        for future in as_completed(futures):
            d = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f'  task {d} crashed: {e}')
            n_done += 1
            if n_done % 25 == 0 or n_done == len(dates):
                print(f'  ... {n_done}/{len(dates)} complete')

    print(f'\n=== finished {interval}: {n_done} dates from {start_yyyy_mm_dd} to {end_yyyy_mm_dd} ===')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <interval>")
        print(f"  interval: day | week | month | year")
        sys.exit(1)

    interval = sys.argv[1]
    upload_date_range(interval, '2017-08-08', '2026-04-30')
