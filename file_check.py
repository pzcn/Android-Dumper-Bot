import requests
import re
import sys
import struct
import urllib
import hashlib
import os
from payload_dumper import http_file
import zipfile 

def get_file_header(url):
    """
    获取文件的前 4 个字节。

    Args:
        url: 文件的 URL。

    Returns:
        如果成功获取文件头，则返回 requests.Response 对象，否则返回 None。
    """
    try:
        headers = {'Range': 'bytes=0-4'}
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        return response
    except requests.RequestException as e:
        print('ERROR:')
        print(f'Unable to get file information, please check the URL\n无法获取文件信息，请检查 URL')
        print(str(e))
        print('ERROR_END')
        return None

def check_zip_file(url):
    """
    检查给定的 URL 是否指向一个有效的 Chrome OS 更新 zip 包。

    Args:
        url: zip 包的 URL。

    Returns:
        如果 URL 有效，则返回 True，否则返回 False。
    """
    url_regex = re.compile(
        r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+')
    
    if not re.match(url_regex, url):
        print('ERROR:')
        print(f'Invalid URL: {url}\n无效的 URL: {url}')
        print('ERROR_END')
        return False

    try:
        with http_file.HttpFile(url) as f:
            with zipfile.ZipFile(f, "r") as zip_file:
                with zip_file.open("payload.bin", "r") as payload_file:
                    # 检查文件签名
                    magic = payload_file.read(4)
                    if magic != b"CrAU":
                        print('ERROR:')
                        print(f'The provided URL does not point to a valid Chrome OS payload.\n提供的 URL 不指向一个有效的 Chrome OS payload。')
                        print('ERROR_END')
                        return False

                    # 检查文件格式版本
                    version_bytes = payload_file.read(8)
                    file_format_version = struct.unpack(">Q", version_bytes)[0]
                    if file_format_version != 2:
                        print('ERROR:')
                        print(f'Unsupported Chrome OS payload version: {file_format_version}\n不支持的 Chrome OS payload 版本: {file_format_version}')
                        print('ERROR_END')
                        return False

                    return True
    except KeyError:
        print('ERROR:')
        print(f'The provided zip file does not a "payload.bin" ROM.\n提供的 zip 文件不是一个payload.bin格式的ROM。')
        print('ERROR_END')
        return False
    except Exception as e:
        print('ERROR:')
        print(f"Error verifying URL: {e}\n验证 URL 时出错: {e}")
        print('ERROR_END')
        return False

def get_filename_from_url(url):
    try:
        response = get_file_header(url)
        filename = None

        response_code = requests.head(url, allow_redirects=True)
        # 检查HTTP状态码，如果不是200或301/302，则返回None
        if response_code is None or response_code.status_code not in [200, 301, 302]:
            print('ERROR:', file=sys.stderr)
            print('Failed to get file name: Invalid HTTP status code', file=sys.stderr)
            print('获取文件名失败: 无效的HTTP状态码',  file=sys.stderr)
            print('ERROR_END', file=sys.stderr)
            return None

        if response:
            content_disposition = response.headers.get('Content-Disposition')
            if content_disposition:
                try:
                    options = content_disposition.split(';')
                    results = [*filter(lambda x: x.strip().startswith('filename'), options)]
                    if results:
                        filename = results[0].split('=')[1].strip()
                except (ValueError, IndexError) as e:
                    print('ERROR:', file=sys.stderr)
                    print('Failed to parse Content-Disposition header:', str(e), file=sys.stderr)
                    print('解析 Content-Disposition 头部信息时出错:', str(e), file=sys.stderr)
                    print('ERROR_END', file=sys.stderr)
                    return None

        if not filename:
            zip_match = re.search(r'([^/]*)\.zip(\?.*)?$', url)
            if zip_match:
                filename = zip_match.group(1)
            else:
                path = urllib.parse.urlsplit(url).path
                filename, ext = os.path.splitext(os.path.basename(path))
                filename = filename + ext if filename else None

        if filename:
            filename = re.sub(r'[<>:"/\\|?*]', '', filename)

        if filename and len(filename) > 20:
            return filename
        elif filename:
            md5_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            return f"{filename}_{md5_hash}"
        else:
            return hashlib.md5(url.encode()).hexdigest()[:8]
    except Exception as e:
        print('ERROR:', file=sys.stderr)
        print(f"Error in get_filename_from_url: {str(e)}", file=sys.stderr)
        print(f"获取文件名时出错: {str(e)}", file=sys.stderr)
        print('ERROR_END', file=sys.stderr)
        return None