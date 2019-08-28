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
