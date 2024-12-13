from flask import Flask, request, redirect, url_for, render_template, Response, send_from_directory
import subprocess
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dump')
def dump():
    arg1 = request.args.get('p')
    arg2 = request.args.get('u')
    return render_template('index.html', arg1=arg1, arg2=arg2, show_output=True)

@app.route('/stream')
def stream():
    arg1 = request.args.get('p')
    arg2 = request.args.get('u')

    if not arg1 or not arg2:
        return "Missing parameters", 400

    def generate():
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"  # 禁用子进程缓冲
        process = subprocess.Popen(['python3', 'queue_scripts.py', '--dump', arg1, arg2], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, text=True, env=env)

        for line in iter(process.stdout.readline, ''):
            yield f"data: {line.strip()}\n\n"
        process.stdout.close()

        process.wait()
        yield "data: SCRIPT_FINISHED\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/download/<path:filename>')
def download(filename):
    # 提取子目录和文件名
    subdir, filename = os.path.split(filename)
    directory = os.path.join(app.root_path, 'output', subdir)  # 合成新的目录路径
    return send_from_directory(directory=directory, path=filename, as_attachment=True)


@app.route('/submit', methods=['POST'])
def submit():
    arg1 = request.form['arg1']
    arg2 = request.form['arg2']
    return redirect(url_for('dump', p=arg1, u=arg2))

if __name__ == '__main__':
    from gevent import pywsgi
    server = pywsgi.WSGIServer(('0.0.0.0',5000),app)
    server.serve_forever()
