import logging
import re
from collections import namedtuple
from pathlib import Path
from urllib.parse import urljoin

import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import BASE_DIR, EXPECTED_STATUS, MAIN_DOC_URL, MAIN_PEP_URL
from outputs import control_output
from utils import find_tag, get_response


def whats_new(session):
    whats_new_url = urljoin(MAIN_DOC_URL, 'whatsnew/')
    response = get_response(session, whats_new_url)
    if response is None:
        return

    response.encoding = 'utf-8'
    
    soup = BeautifulSoup(response.text, features='lxml')
    section = find_tag(soup, 'section', attrs={'id': 'what-s-new-in-python'})
    div_with_ul = find_tag(section, 'div', attrs={'class':'toctree-wrapper'})
    li_list = div_with_ul.find_all('li', attrs={'class':'toctree-l1'})
    
    result = [('Ссылка на статью', 'Заголовок', 'Редактор, Автор')]
    for li in tqdm(li_list):
        version_a_tag = find_tag(li, 'a')
        href = version_a_tag['href']
        full_url = urljoin(whats_new_url, href)
        response = session.get(full_url)    
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, features='lxml')
        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        result.append((full_url, h1.text, dl.text.replace("\n", "")))
    return result

def latest_versions(session):
    response = get_response(session, MAIN_DOC_URL)
    if response is None:
        return

    response.encoding = 'utf-8'
    
    soup = BeautifulSoup(response.text, features='lxml')
    sidebar = find_tag(soup, 'div', attrs={'class':'sphinxsidebarwrapper'})
    ul_tags = sidebar.find_all('ul')
    
    a_tags = []
    for ul in ul_tags:
        if 'All version' in ul.text:
            a_tags = ul.find_all('a')
            break
        else:
            raise Exception('Ничего не найдено')
    
    results = [('Ссылка на документацию', 'Версия', 'Статус')]
    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'

    for a in a_tags:
        link = a['href']
        re_match = re.search(pattern, a.text)
        version = a.text
        status = ''

        if re_match:
            version, status = re_match.groups()

        results.append((link, version, status))
    return results

def download(session):
    downloads_url = urljoin(MAIN_DOC_URL, 'download.html')
    response = get_response(session, downloads_url)
    if response is None:
        return
    
    soup = BeautifulSoup(response.text, features='lxml')
    main_div = find_tag(soup, 'div', attrs={'role': 'main'})
    table = find_tag(main_div, 'table', attrs={'class': 'docutils'})
    pdf_a4_a_tag = find_tag(table, 'a', attrs={
        'href': re.compile(r'.+pdf-a4\.zip$')
    })
    file_link = pdf_a4_a_tag['href']
    download_link = urljoin(downloads_url, file_link)

    file_name = file_link.split('/')[-1]
    download_dir = BASE_DIR / 'downloads'
    archive_path = download_dir / file_name 
    Path.mkdir(download_dir, exist_ok=True)

    response = session.get(download_link)
    with open(archive_path, 'wb') as file:
        file.write(response.content)
    logging.info(f'Архив был загружен и сохранён: {archive_path}') 

def __get_different_peps(session):
    different_peps = set()
    PEPItem = namedtuple('pep', ['statuses', 'link'])
    response = get_response(session, MAIN_PEP_URL)
    if response is None:
        return

    response.encoding = 'utf-8'

    soup = BeautifulSoup(response.text, features='lxml')
    pep_tables = soup.find_all(
        'table',
        class_='pep-zero-table'
    )

    for table in tqdm(pep_tables, desc='Анализ таблиц со списком PEP'):
        tbody_tag = find_tag(table, 'tbody')
        tr_list = tbody_tag.find_all(
            'tr',
            attrs={'class': ['row-even', 'row-odd']}
        )

        for tr in tr_list:
            first_td = find_tag(tr, 'td')
            abbr_tag = first_td.find('abbr')
            preview_status = ''
            if abbr_tag is not None:
                preview_status = abbr_tag.text[1:]

            second_td = first_td.find_next_sibling('td')
            a_tag = find_tag(second_td, 'a', attrs={'class': 'pep'})
            pep_href = a_tag['href']
            pep_link = urljoin(MAIN_PEP_URL, pep_href)
            
            different_peps.add(
                PEPItem(EXPECTED_STATUS[preview_status], pep_link)
            )

    return different_peps

def pep(session):
    pep_type_count = {}
    mismatched_statuses = []
    different_peps = __get_different_peps(session)
    results = [('Статус', 'Количество')]
    
    for pep in tqdm(different_peps, desc='Анализ страниц PEP'):
        response = get_response(session, pep.link)
        if response is None:
            continue
        response.encoding = 'utf-8'

        soup = BeautifulSoup(response.text, features='lxml')
        field_list = find_tag(soup, 'dl', attrs={'class': 'field-list'})
        status_tag = field_list.select_one(':-soup-contains("Status")')
        status = status_tag.find_next_sibling('dd').text
        
        if status not in pep_type_count:
            pep_type_count[status] = 0

        pep_type_count[status] += 1
        
        if status not in pep.statuses:
            mismatched_statuses.append(
                {
                    'table_status': pep.statuses,
                    'page_status': status,
                    'link': pep.link,
                }
            )
    
    pep_type_count = dict(sorted(pep_type_count.items()))
    for pep_status, pep_count in pep_type_count.items():
        results.append((pep_status, pep_count),)

    if len(mismatched_statuses) > 0:
        logging.info('Несовпадающие статусы:')
        for pep in mismatched_statuses:
            logging.info(pep['link'])
            logging.info(f'Статус в карточке: {pep["page_status"]}')
            logging.info(
                f'Ожидаемые статусы: {', '.join(pep["table_status"])}'
            )

    results.append(('Total', len(different_peps)),)
    return results

MODE_TO_FUNCTION = {
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download,
    'pep': pep,
}

def main():
    configure_logging()
    logging.info('Парсер запущен')
    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()
    parser_mode = args.mode
    logging.info(f'Аргументы коммандной строки: {args}')
    session = requests_cache.CachedSession()
    if (args.clear_cache):
        session.cache.clear()
    
    result = MODE_TO_FUNCTION[parser_mode](session)

    if result is not None:
        control_output(result, args)

    logging.info('Парсер завершил работу')

if __name__ == '__main__':
    main() 