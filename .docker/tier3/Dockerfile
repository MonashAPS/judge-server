FROM dmoj/runtimes-tier3

ARG TAG=master
# https://serverfault.com/questions/1074688/security-debian-org-does-not-have-a-release-file-on-with-debian-docker-images
# https://github.com/DMOJ/runtimes-docker/blob/345c671a441ffd6d4a0928d9182236374dfb795a/tier1/Dockerfile
RUN echo deb http://deb.debian.org/debian/ bullseye main > /etc/apt/sources.list.d/bullseye.list && \
    echo deb http://security.debian.org/debian-security bullseye-security main >> /etc/apt/sources.list.d/bullseye.list && \
    echo deb http://deb.debian.org/debian/ experimental main > /etc/apt/sources.list.d/experimental.list && \
		apt-get update
RUN apt-get install -y python3.9-venv
RUN mkdir /judge /problems && cd /judge && \
	curl -L https://github.com/MonashAPS/judge-server/archive/"${TAG}".tar.gz | tar -xz --strip-components=1 && \
    python3 -m venv --prompt=DMOJ /env && \
	/env/bin/pip3 install cython && \
	/env/bin/pip3 install -e . && \
	/env/bin/python3 setup.py develop && \
	HOME=~judge . ~judge/.profile && \
	runuser -u judge -w PATH -- /env/bin/dmoj-autoconf -V > /judge-runtime-paths.yml && \
	echo '  crt_x86_in_lib32: true' >> /judge-runtime-paths.yml

ENTRYPOINT ["/usr/bin/tini", "--", "/judge/.docker/entry"]
