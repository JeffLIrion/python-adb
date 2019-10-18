.PHONY: release
release:
	rm -rf dist
	python setup.py sdist bdist_wheel
	twine upload dist/*
	
.PHONY: docs
docs:
	@cd docs/source && rm -f adb*.rst && mkdir -p _static
	@cd docs && sphinx-apidoc -f -e -o source/ ../adb/
	@cd docs && make html && make html

.PHONY: coverage
coverage:
	coverage run --source adb setup.py test && coverage html && coverage report -m

.PHONY: lint
lint:
	flake8 adb/ && pylint adb/

.PHONY: alltests
alltests:
	flake8 adb/ && pylint adb/ && coverage run --source adb setup.py test && coverage report -m
