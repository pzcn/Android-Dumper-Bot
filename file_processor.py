import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import asyncio
import hashlib
import shlex
import traceback
import urllib.parse

import file_check
import requests

async def run_payload_dumper(tempdir, url, command):
    """运行 payload_dumper 命令并返回输出结果。"""
    try:
        args = shlex.split(command.format(temp_dir=tempdir))
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        try:
            await asyncio.wait_for(process.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            print('ERROR:', file=sys.stdout)
            print('Download timed out, please try again or change URL', file=sys.stdout)
            print('下载超时，请重试或更换链接', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            return 1

        if process.returncode != 0:
            error_message = stderr.decode().strip().split('\n')[-1]  # 获取最后一行错误信息
            print('ERROR:', file=sys.stdout)
            print('payload_dumper execution failed:', file=sys.stdout)
            print('payload_dumper 执行失败:', file=sys.stdout)
            print(f'{error_message}', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            return 1

        print(stdout.decode(), file=sys.stdout)  # 直接打印标准输出到控制台
        return 0
    except Exception as e:
        error_message = traceback.format_exc().strip().split('\n')[-1]  # 获取最后一行错误信息
        print('ERROR:', file=sys.stdout)
        print(f"Unknown error", file=sys.stdout)
        print(f"未知错误", file=sys.stdout)
        print(f'{error_message}', file=sys.stdout)
        print('ERROR_END', file=sys.stdout)
        return 1

def list_partitions(url, outputdir='output'):
    """列出分区信息并保存到文件。"""
    filename = "partitions_info"
    extension = ".json"
    subdir = "partitions"
    print('STATUS:', file=sys.stdout)
    print("Listing partitions...", file=sys.stdout)
    print("正在列出分区信息", file=sys.stdout)
    print('STATUS_END', file=sys.stdout)
    try:
        URLfilename = file_check.get_filename_from_url(url)
        if URLfilename is None:
            print(f"获取文件名失败", file=sys.stdout)
            return
        output_path = os.path.join(outputdir, subdir, f"{URLfilename}{extension}")

        if os.path.exists(output_path):
            print(f'FILE:{output_path}')
            return 0

        tempdir = tempfile.mkdtemp()

        command = f'/home/ubuntu/.local/bin/payload_dumper --out {tempdir} --list "{url}"'
        exit_code = asyncio.run(run_payload_dumper(tempdir, url, command))  # 直接执行命令
        if exit_code != 0:
            shutil.rmtree(tempdir)
            return 1

        temp_output_path = os.path.join(tempdir, f"{filename}{extension}")

        if os.path.isfile(temp_output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            shutil.move(temp_output_path, output_path)
            print(f'FILE:{output_path}')
            return 0
        else:
            print('ERROR:', file=sys.stdout)
            print('Partition information file not found', file=sys.stdout)
            print('未找到分区信息文件', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            shutil.rmtree(tempdir)
            return 1
    except Exception as e:
        print('ERROR:', file=sys.stdout)
        print(f"Error in list_partitions: {str(e)}", file=sys.stdout)
        print(f"列出分区信息时出错: {str(e)}", file=sys.stdout)
        print('ERROR_END', file=sys.stdout)
        return 1

def dump_partition(url, partition_name, outputdir='output'):
    """导出指定分区并压缩保存。"""
    filename = partition_name
    extension = ".zip"
    subdir = f"zip/{partition_name}"

    try:
        URLfilename = file_check.get_filename_from_url(url)
        if URLfilename is None:
            print(f"获取文件名失败", file=sys.stdout)
            return
        output_path = os.path.join(outputdir, subdir, f"{filename}_{URLfilename}{extension}")

        if os.path.exists(output_path):
            print('STATUS:', file=sys.stdout)
            print('Found cached file, uploading...', file=sys.stdout)
            print('找到缓存文件，正在上传...', file=sys.stdout)
            print('STATUS_END', file=sys.stdout)
            print(f'FILE:{output_path}')
            return 0

        tempdir = tempfile.mkdtemp()
        print('STATUS:', file=sys.stdout)
        print('Dumping partition...', file=sys.stdout)
        print('正在提取分区...', file=sys.stdout)
        print('STATUS_END', file=sys.stdout)

        command = f'/home/ubuntu/.local/bin/payload_dumper --out {tempdir} --partitions {partition_name} "{url}"'
        exit_code = asyncio.run(run_payload_dumper(tempdir, url, command))  # 直接执行命令
        if exit_code != 0:
            shutil.rmtree(tempdir)
            return 1

        temp_output_path = os.path.join(tempdir, f"{filename}.img")

        if os.path.isfile(temp_output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(temp_output_path, arcname=f"{filename}.img")
            zip_file_size = os.path.getsize(output_path)
            if zip_file_size > 50 * 1000 * 1000:
                print('ERROR:', file=sys.stdout)
                print('Compressed file size exceeds 50 MB, unable to upload.', file=sys.stdout)
                print('压缩后的文件大小超过了 50 MB，无法上传。', file=sys.stdout)
                print('ERROR_END', file=sys.stdout)
                os.remove(output_path)
                return 1
            print(f'FILE:{output_path}')
            return 0
        else:
            print('ERROR:', file=sys.stdout)
            print('Partition image file not found: {temp_output_path}', file=sys.stdout)
            print('未找到分区镜像文件: {temp_output_path}', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            shutil.rmtree(tempdir)
            return 1
    except Exception as e:
        print('ERROR:', file=sys.stdout)
        print(f"Error in dump_partition: {str(e)}", file=sys.stdout)
        print(f"导出分区时出错: {str(e)}", file=sys.stdout)
        print('ERROR_END', file=sys.stdout)
        return 1

def fetch_metadata(url, outputdir='output'):
    """获取元数据并保存到文件。"""
    filename = "metadata"
    extension = ""
    subdir = "metadata"

    try:
        URLfilename = file_check.get_filename_from_url(url)
        if URLfilename is None:
            print(f"获取文件名失败", file=sys.stdout)
            return
        output_path = os.path.join(outputdir, subdir, f"{URLfilename}{extension}")

        if os.path.exists(output_path):
            print(f'FILE:{output_path}')
            return 0

        tempdir = tempfile.mkdtemp()
        print('STATUS:', file=sys.stdout)
        print('Fetching metadata...', file=sys.stdout)
        print('正在获取元数据...', file=sys.stdout)
        print('STATUS_END', file=sys.stdout)

        command = f'/home/ubuntu/.local/bin/payload_dumper --out {tempdir} --metadata "{url}"'
        exit_code = asyncio.run(run_payload_dumper(tempdir, url, command))  # 直接执行命令
        if exit_code != 0:
            shutil.rmtree(tempdir)
            return 1

        temp_output_path = os.path.join(tempdir, f"{filename}{extension}")

        if os.path.isfile(temp_output_path):
            print('STATUS:', file=sys.stdout)
            print('Metadata file found, saving...', file=sys.stdout)
            print('找到元数据文件，正在保存...', file=sys.stdout)
            print('STATUS_END', file=sys.stdout)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            shutil.move(temp_output_path, output_path)
            print(f'FILE:{output_path}')
            return 0
        else:
            print('ERROR:', file=sys.stdout)
            print('Metadata file not found: {temp_output_path}', file=sys.stdout)
            print('未找到元数据文件: {temp_output_path}', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            shutil.rmtree(tempdir)
            return 1
    except Exception as e:
        print('ERROR:', file=sys.stdout)
        print(f"Error in fetch_metadata: {str(e)}", file=sys.stdout)
        print(f"获取元数据时出错: {str(e)}", file=sys.stdout)
        print('ERROR_END', file=sys.stdout)
        return 1

def main():
    try:
        if len(sys.argv) < 3:
            print('ERROR:', file=sys.stdout)
            print('Invalid command. Usage: <script> <command> <url> [<partition_name>]', file=sys.stdout)
            print('无效的命令. 使用方法: <script> <command> <url> [<partition_name>]', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            sys.exit(1)

        command = sys.argv[1]

        if command == '--dump':
            if len(sys.argv) < 4:
                print('ERROR:', file=sys.stdout)
                print('Invalid command. Usage: <script> --dump <url> <partition_name>', file=sys.stdout)
                print('无效的命令. 使用方法: <script> --dump <url> <partition_name>', file=sys.stdout)
                print('ERROR_END', file=sys.stdout)
                sys.exit(1)
            partition_name = sys.argv[2]
            url = sys.argv[3].strip('"')
            if not file_check.check_zip_file(url): 
                sys.exit(1)
            if not re.match(r'^[a-zA-Z0-9_]+$', partition_name):
                print('ERROR:', file=sys.stdout)
                print(f'Invalid partition name: {partition_name}', file=sys.stdout)
                print(f'无效的分区名称: {partition_name}', file=sys.stdout)
                print('ERROR_END', file=sys.stdout)
                sys.exit(1)
            
            invalid_partitions = ['modem', 'modemfirmware', 'odm', 'product', 'system', 'system_ext', 'vendor']
            if partition_name in invalid_partitions:
                print('ERROR:', file=sys.stdout)
                print(f'Invalid partition name: {partition_name}', file=sys.stdout)
                print(f'无效的分区名称: {partition_name}', file=sys.stdout)
                print('ERROR_END', file=sys.stdout)
                sys.exit(1)

            exit_code = dump_partition(url, partition_name)
            sys.exit(exit_code)  # 根据返回值退出

        elif command == '--metadata':
            url = sys.argv[2].strip('"')
            if not file_check.check_zip_file(url): 
                sys.exit(1)
            exit_code = fetch_metadata(url)
            sys.exit(exit_code)  # 根据返回值退出

        elif command == '--list':
            url = sys.argv[2].strip('"')
            if not file_check.check_zip_file(url): 
                sys.exit(1)
            exit_code = list_partitions(url)
            sys.exit(exit_code)  # 根据返回值退出

        else:
            print('ERROR:', file=sys.stdout)
            print('Unknown command', file=sys.stdout)
            print('未知命令', file=sys.stdout)
            print('ERROR_END', file=sys.stdout)
            sys.exit(1)
    except Exception as e:
        print('ERROR:', file=sys.stdout)
        print(f"Error in main: {str(e)}", file=sys.stdout)
        print(f"主函数中出错: {str(e)}", file=sys.stdout)
        print('ERROR_END', file=sys.stdout)
        sys.exit(1)

if __name__ == '__main__':
    main()
