FROM python:3.9-slim

RUN apt-get -y update && apt-get -y upgrade && apt-get install -y git ffmpeg

WORKDIR /home

COPY requirements.txt .
COPY .env .

RUN pip install -r requirements.txt

RUN echo "hellokjhfsdkj"
RUN git clone https://github.com/johnmwalker/TheBumApp.git

WORKDIR /home/TheBumApp

CMD ["python3", "bot.py"]