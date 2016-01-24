venv: venv/bin/activate
venv/bin/activate: requirements.txt
	test -d venv || virtualenv venv -p python3
	. venv/bin/activate; pip install -Ur requirements.txt
	touch venv/bin/activate

clean:
	find -name '*.pyc' -delete

distclean: clean
	rm -rf venv
