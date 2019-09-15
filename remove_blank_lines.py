"""Remove blank lines from a file.

"""

import sys

def do_file(fname):
    """ Run on just one file.
    """
    with open(fname) as f:
        lines = f.readlines()

    with open(fname, "w") as g:
        for line, nextline in zip(lines, lines[1:] + ['END OF FILE\n']):
            # If the line is not blank, write it
            if line.strip():
                g.write(line)

            # If the line is blank...
            else:
                # If the next line is not indented, write a blank line
                if nextline.strip() and not nextline.startswith(' '):
                    g.write('\n')
                    continue
                
                # If the next line defines a function, class, or method, write a blank line
                nextline_stripped = nextline.strip()
                if nextline_stripped.startswith('@') or nextline_stripped.startswith('class ') or nextline_stripped.startswith('def '):
                    g.write('\n')

if __name__ == '__main__':
    do_file(sys.argv[1])
