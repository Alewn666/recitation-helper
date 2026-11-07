FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai PORT=8090
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
COPY app.py .
COPY templates ./templates
COPY static ./static
RUN mkdir -p /data
EXPOSE 8090
CMD ["gunicorn", "-w", "1", "-k", "gthread", "--threads", "8", "-b", "0.0.0.0:8090", "--timeout", "120", "app:app"]
