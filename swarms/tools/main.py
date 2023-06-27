from enum import Enum
from typing import Callable, Tuple

from langchain.agents.agent import AgentExecutor
from langchain.agents.tools import BaseTool, Tool



from typing import Optional

from langchain.agents import load_tools
from langchain.agents.tools import BaseTool
from langchain.llms.base import BaseLLM


import requests
from bs4 import BeautifulSoup
from llama_index import GPTSimpleVectorIndex
from llama_index.readers.database import DatabaseReader

from env import settings
from logger import logger



class ToolScope(Enum):
    GLOBAL = "global"
    SESSION = "session"


SessionGetter = Callable[[], Tuple[str, AgentExecutor]]


def tool(
    name: str,
    description: str,
    scope: ToolScope = ToolScope.GLOBAL,
):
    def decorator(func):
        func.name = name
        func.description = description
        func.is_tool = True
        func.scope = scope
        return func

    return decorator


class ToolWrapper:
    def __init__(self, name: str, description: str, scope: ToolScope, func):
        self.name = name
        self.description = description
        self.scope = scope
        self.func = func

    def is_global(self) -> bool:
        return self.scope == ToolScope.GLOBAL

    def is_per_session(self) -> bool:
        return self.scope == ToolScope.SESSION

    def to_tool(
        self,
        get_session: SessionGetter = lambda: [],
    ) -> BaseTool:
        func = self.func
        if self.is_per_session():
            func = lambda *args, **kwargs: self.func(
                *args, **kwargs, get_session=get_session
            )

        return Tool(
            name=self.name,
            description=self.description,
            func=func,
        )


class BaseToolSet:
    def tool_wrappers(cls) -> list[ToolWrapper]:
        methods = [
            getattr(cls, m) for m in dir(cls) if hasattr(getattr(cls, m), "is_tool")
        ]
        return [ToolWrapper(m.name, m.description, m.scope, m) for m in methods]
    



class RequestsGet(BaseToolSet):
    @tool(
        name="Requests Get",
        description="A portal to the internet. "
        "Use this when you need to get specific content from a website."
        "Input should be a  url (i.e. https://www.google.com)."
        "The output will be the text response of the GET request.",
    )
    def get(self, url: str) -> str:
        """Run the tool."""
        html = requests.get(url).text
        soup = BeautifulSoup(html)
        non_readable_tags = soup.find_all(
            ["script", "style", "header", "footer", "form"]
        )

        for non_readable_tag in non_readable_tags:
            non_readable_tag.extract()

        content = soup.get_text("\n", strip=True)

        if len(content) > 300:
            content = content[:300] + "..."

        logger.debug(
            f"\nProcessed RequestsGet, Input Url: {url} " f"Output Contents: {content}"
        )

        return content


class WineDB(BaseToolSet):
    def __init__(self):
        db = DatabaseReader(
            scheme="postgresql",  # Database Scheme
            host=settings["WINEDB_HOST"],  # Database Host
            port="5432",  # Database Port
            user="alphadom",  # Database User
            password=settings["WINEDB_PASSWORD"],  # Database Password
            dbname="postgres",  # Database Name
        )
        self.columns = ["nameEn", "nameKo", "description"]
        concat_columns = str(",'-',".join([f'"{i}"' for i in self.columns]))
        query = f"""
            SELECT
                Concat({concat_columns})
            FROM wine
        """
        documents = db.load_data(query=query)
        self.index = GPTSimpleVectorIndex(documents)

    @tool(
        name="Wine Recommendation",
        description="A tool to recommend wines based on a user's input. "
        "Inputs are necessary factors for wine recommendations, such as the user's mood today, side dishes to eat with wine, people to drink wine with, what things you want to do, the scent and taste of their favorite wine."
        "The output will be a list of recommended wines."
        "The tool is based on a database of wine reviews, which is stored in a database.",
    )
    def recommend(self, query: str) -> str:
        """Run the tool."""
        results = self.index.query(query)
        wine = "\n".join(
            [
                f"{i}:{j}"
                for i, j in zip(
                    self.columns, results.source_nodes[0].source_text.split("-")
                )
            ]
        )
        output = results.response + "\n\n" + wine

        logger.debug(
            f"\nProcessed WineDB, Input Query: {query} " f"Output Wine: {wine}"
        )

        return output


class ExitConversation(BaseToolSet):
    @tool(
        name="Exit Conversation",
        description="A tool to exit the conversation. "
        "Use this when you want to exit the conversation. "
        "The input should be a message that the conversation is over.",
        scope=ToolScope.SESSION,
    )
    def exit(self, message: str, get_session: SessionGetter) -> str:
        """Run the tool."""
        _, executor = get_session()
        del executor

        logger.debug(f"\nProcessed ExitConversation.")

        return message
    




class ToolsFactory:
    @staticmethod
    def from_toolset(
        toolset: BaseToolSet,
        only_global: Optional[bool] = False,
        only_per_session: Optional[bool] = False,
        get_session: SessionGetter = lambda: [],
    ) -> list[BaseTool]:
        tools = []
        for wrapper in toolset.tool_wrappers():
            if only_global and not wrapper.is_global():
                continue
            if only_per_session and not wrapper.is_per_session():
                continue
            tools.append(wrapper.to_tool(get_session=get_session))
        return tools

    @staticmethod
    def create_global_tools(
        toolsets: list[BaseToolSet],
    ) -> list[BaseTool]:
        tools = []
        for toolset in toolsets:
            tools.extend(
                ToolsFactory.from_toolset(
                    toolset=toolset,
                    only_global=True,
                )
            )
        return tools

    @staticmethod
    def create_per_session_tools(
        toolsets: list[BaseToolSet],
        get_session: SessionGetter = lambda: [],
    ) -> list[BaseTool]:
        tools = []
        for toolset in toolsets:
            tools.extend(
                ToolsFactory.from_toolset(
                    toolset=toolset,
                    only_per_session=True,
                    get_session=get_session,
                )
            )
        return tools

    @staticmethod
    def create_global_tools_from_names(
        toolnames: list[str],
        llm: Optional[BaseLLM],
    ) -> list[BaseTool]:
        return load_tools(toolnames, llm=llm)
    
##########################################+> 





##########################################+>  SYS
import signal
from typing import Optional, Tuple

from ptrace.debugger import (
    NewProcessEvent,
    ProcessExecution,
    ProcessExit,
    ProcessSignal,
    PtraceDebugger,
    PtraceProcess,
)
from ptrace.func_call import FunctionCallOptions
from ptrace.syscall import PtraceSyscall
from ptrace.tools import signal_to_exitcode


class SyscallTimeoutException(Exception):
    def __init__(self, pid: int, *args) -> None:
        super().__init__(f"deadline exceeded while waiting syscall for {pid}", *args)


class SyscallTracer:
    def __init__(self, pid: int):
        self.debugger: PtraceDebugger = PtraceDebugger()
        self.pid: int = pid
        self.process: PtraceProcess = None

    def is_waiting(self, syscall: PtraceSyscall) -> bool:
        if syscall.name.startswith("wait"):
            return True
        return False

    def attach(self):
        self.process = self.debugger.addProcess(self.pid, False)

    def detach(self):
        self.process.detach()
        self.debugger.quit()

    def set_timer(self, timeout: int):
        def handler(signum, frame):
            raise SyscallTimeoutException(self.process.pid)

        signal.signal(signal.SIGALRM, handler)
        signal.alarm(timeout)

    def reset_timer(self):
        signal.alarm(0)

    def wait_syscall_with_timeout(self, timeout: int):
        self.set_timer(timeout)
        self.process.waitSyscall()
        self.reset_timer()

    def wait_until_stop_or_exit(self) -> Tuple[Optional[int], str]:
        self.process.syscall()
        exitcode = None
        reason = ""
        while True:
            if not self.debugger:
                break

            try:
                self.wait_syscall_with_timeout(30)
            except ProcessExit as event:
                if event.exitcode is not None:
                    exitcode = event.exitcode
                continue
            except ProcessSignal as event:
                event.process.syscall(event.signum)
                exitcode = signal_to_exitcode(event.signum)
                reason = event.reason
                continue
            except NewProcessEvent as event:
                continue
            except ProcessExecution as event:
                continue
            except Exception as e:
                reason = str(e)
                break

            syscall = self.process.syscall_state.event(
                FunctionCallOptions(
                    write_types=False,
                    write_argname=False,
                    string_max_length=300,
                    replace_socketcall=True,
                    write_address=False,
                    max_array_count=20,
                )
            )

            self.process.syscall()

            if syscall is None:
                continue

            if syscall.result:
                continue

        self.reset_timer()

        return exitcode, reason
    ##########################################+> SYS CALL END



############### => st dout.py

import os
import time
import subprocess
from datetime import datetime
from typing import Callable, Literal, Optional, Union, Tuple

PipeType = Union[Literal["stdout"], Literal["stderr"]]


class StdoutTracer:
    def __init__(
        self,
        process: subprocess.Popen,
        timeout: int = 30,
        interval: int = 0.1,
        on_output: Callable[[PipeType, str], None] = lambda: None,
    ):
        self.process: subprocess.Popen = process
        self.timeout: int = timeout
        self.interval: int = interval
        self.last_output: datetime = None
        self.on_output: Callable[[PipeType, str], None] = on_output

    def nonblock(self):
        os.set_blocking(self.process.stdout.fileno(), False)
        os.set_blocking(self.process.stderr.fileno(), False)

    def get_output(self, pipe: PipeType) -> str:
        output = None
        if pipe == "stdout":
            output = self.process.stdout.read()
        elif pipe == "stderr":
            output = self.process.stderr.read()

        if output:
            decoded = output.decode()
            self.on_output(pipe, decoded)
            self.last_output = datetime.now()
            return decoded
        return ""

    def last_output_passed(self, seconds: int) -> bool:
        return (datetime.now() - self.last_output).seconds > seconds

    def wait_until_stop_or_exit(self) -> Tuple[Optional[int], str]:
        self.nonblock()
        self.last_output = datetime.now()
        output = ""
        exitcode = None
        while True:
            new_stdout = self.get_output("stdout")
            if new_stdout:
                output += new_stdout

            new_stderr = self.get_output("stderr")
            if new_stderr:
                output += new_stderr

            if self.process.poll() is not None:
                exitcode = self.process.poll()
                break

            if self.last_output_passed(self.timeout):
                self.process.kill()
                break

            time.sleep(self.interval)

        return (exitcode, output)

################## => stdout end

import os
import subprocess
import time
from datetime import datetime
from typing import Dict, List

from ansi import ANSI, Color, Style
from env import settings
from logger import logger


class Terminal(BaseToolSet):
    def __init__(self):
        self.sessions: Dict[str, List[SyscallTracer]] = {}

    @tool(
        name="Terminal",
        description="Executes commands in a terminal."
        "If linux errno occurs, we have to solve the problem with the terminal. "
        "Input must be one valid command. "
        "Output will be any output from running that command.",
        scope=ToolScope.SESSION,
    )
    def execute(self, commands: str, get_session: SessionGetter) -> str:
        session, _ = get_session()

        try:
            process = subprocess.Popen(
                commands,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            logger.info(ANSI("Realtime Terminal Output").to(Color.magenta()) + ": ")

            output = ""
            tracer = StdoutTracer(
                process,
                on_output=lambda p, o: logger.info(
                    ANSI(p).to(Style.dim()) + " " + o.strip("\n")
                ),
            )
            exitcode, output = tracer.wait_until_stop_or_exit()
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed Terminal, Input Commands: {commands} "
            f"Output Answer: {output}"
        )
        return output


if __name__ == "__main__":
    import time

    o = Terminal().execute(
        "sleep 1; echo 1; sleep 2; echo 2; sleep 3; echo 3; sleep 10;",
        lambda: ("", None),
    )
    print(o)

    time.sleep(10)  # see if timer has reset


###################=> EDITOR/VERIFY
from pathlib import Path


def verify(func):
    def wrapper(*args, **kwargs):
        try:
            filepath = args[0].filepath
        except AttributeError:
            raise Exception("This tool doesn't have filepath. Please check your code.")
        if not str(Path(filepath).resolve()).startswith(str(Path().resolve())):
            return "You can't access file outside of playground."
        return func(*args, **kwargs)

    return wrapper
#=====================> EDITOR/END VERIFY


###### EDITOR/WRITE.PY

"""
write protocol:

<filepath>
<content>
"""
import os



class WriteCommand:
    separator = "\n"

    def __init__(self, filepath: str, content: int):
        self.filepath: str = filepath
        self.content: str = content
        self.mode: str = "w"

    def with_mode(self, mode: str) -> "WriteCommand":
        self.mode = mode
        return self

    @verify
    def execute(self) -> str:
        dir_path = os.path.dirname(self.filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(self.filepath, self.mode) as f:
            f.write(self.content)
        return self.content

    @staticmethod
    def from_str(command: str) -> "WriteCommand":
        filepath = command.split(WriteCommand.separator)[0]
        return WriteCommand(filepath, command[len(filepath) + 1 :])


class CodeWriter:
    @staticmethod
    def write(command: str) -> str:
        return WriteCommand.from_str(command).with_mode("w").execute()

    @staticmethod
    def append(command: str) -> str:
        return WriteCommand.from_str(command).with_mode("a").execute()
    
#================> END 



#============================> EDITOR/READ.PY
"""
read protocol:

<filepath>|<start line>-<end line>
"""
from typing import List, Optional



class Line:
    def __init__(self, content: str, line_number: int, depth: int):
        self.__content: str = content
        self.__line_number: int = line_number
        self.__depth: int = depth
        self.__children: List[Line] = []

    def get_content(self) -> str:
        return self.__content

    def get_depth(self) -> int:
        return self.__depth

    def append_child(self, child: "Line") -> None:
        self.__children.append(child)

    def find_by_lte_depth(self, depth: int) -> List["Line"]:
        if self.__depth > depth:
            return []

        lines: List[Line] = [self]
        for child in self.__children:
            lines += child.find_by_lte_depth(depth)
        return lines

    def find_by_content(self, content: str) -> List["Line"]:
        if content in self.__content:
            return [self]

        lines: List[Line] = []
        for child in self.__children:
            lines += child.find_by_content(content)
        return lines

    def find_last_lines(self) -> List["Line"]:
        if len(self.__children) == 0:
            return [self]
        else:
            return [self, *self.__children[-1].find_last_lines()]

    def print(self, depth: int = 0) -> None:
        print(f"{'  ' * depth}{self}", end="")
        for child in self.__children:
            child.print(depth + 1)

    def __repr__(self):
        return f"{self.__line_number}: {self.__content}"


class CodeTree:
    def __init__(self):
        self.root: Line = Line("\n", -1, -1)

    def append(self, content: str, line_number: int) -> None:
        last_lines: List[Line] = self.root.find_last_lines()
        new_leading_spaces: int = self.__get_leading_spaces(content)

        previous_line: Line = self.root
        previous_leading_spaces: int = -1
        for line in last_lines:
            leading_spaces = self.__get_leading_spaces(line.get_content())
            if (
                previous_leading_spaces < new_leading_spaces
                and new_leading_spaces <= leading_spaces
            ):
                break
            previous_line, previous_leading_spaces = line, leading_spaces

        new_line_depth: int = previous_line.get_depth() + 1
        previous_line.append_child(Line(content, line_number, new_line_depth))

    def find_from_root(self, depth: int) -> List[Line]:
        return self.root.find_by_lte_depth(depth)

    def find_from_parent(self, depth: int, parent_content: str) -> List[Line]:
        lines: List[Line] = self.root.find_by_content(parent_content)
        if len(lines) == 0:
            return []
        parent = lines[0]
        return parent.find_by_lte_depth(depth + parent.get_depth())

    def print(self):
        print("Code Tree:")
        print("=================================")
        self.root.print()
        print("=================================")

    def __get_leading_spaces(self, content: str) -> int:
        return len(content) - len(content.lstrip())


class ReadCommand:
    separator = "|"

    def __init__(self, filepath: str, start: int, end: int):
        self.filepath: str = filepath
        self.start: int = start
        self.end: int = end

    @verify
    def execute(self) -> str:
        with open(self.filepath, "r") as f:
            code = f.readlines()

        if self.start == self.end:
            code = code[self.start - 1]
        else:
            code = "".join(code[self.start - 1 : self.end])
        return code

    @staticmethod
    def from_str(command: str) -> "ReadCommand":
        filepath, line = command.split(ReadCommand.separator)
        start, end = line.split("-")
        return ReadCommand(filepath, int(start), int(end))


class SummaryCommand:
    separator = "|"

    def __init__(self, filepath: str, depth: int, parent_content: Optional[str] = None):
        self.filepath: str = filepath
        self.depth: int = depth
        self.parent_content: Optional[str] = parent_content

    @verify
    def execute(self) -> str:
        with open(self.filepath, "r") as f:
            code = f.readlines()

        code_tree = CodeTree()
        for i, line in enumerate(code):
            if line.strip() != "":
                code_tree.append(line, i + 1)

        if self.parent_content is None:
            lines = code_tree.find_from_root(self.depth)
        else:
            lines = code_tree.find_from_parent(self.depth, self.parent_content)
        return "".join([str(line) for line in lines])

    @staticmethod
    def from_str(command: str) -> "SummaryCommand":
        command_list: List[str] = command.split(SummaryCommand.separator)
        filepath: str = command_list[0]
        depth: int = int(command_list[1])
        parent_content: str | None = command_list[2] if len(command_list) == 3 else None
        return SummaryCommand(
            filepath=filepath, depth=depth, parent_content=parent_content
        )


class CodeReader:
    @staticmethod
    def read(command: str) -> str:
        return ReadCommand.from_str(command).execute()

    @staticmethod
    def summary(command: str) -> str:
        return SummaryCommand.from_str(command).execute()


if __name__ == "__main__":
    summary = CodeReader.summary("read.py|1|class ReadCommand:")
    print(summary)

#============================> EDITOR/READ.PY END




#=================================> EDITOR/PATCH.PY
"""
patch protocol:

<filepath>|<line>,<col>|<line>,<col>|<content>
---~~~+++===+++~~~---
<filepath>|<line>,<col>|<line>,<col>|<content>
---~~~+++===+++~~~---
...
---~~~+++===+++~~~---

let say original code is:
```
import requests

def crawl_news(keyword):
    url = f"https://www.google.com/search?q={keyword}+news"
    response = requests.get(url)

    news = []
    for result in response:
        news.append(result.text)

    return news
```

and we want to change it to:
```
import requests
from bs4 import BeautifulSoup

def crawl_news(keyword):
    url = f"https://www.google.com/search?q={keyword}+news"
    html = requests.get(url).text
    soup = BeautifulSoup(html, "html.parser")
    news_results = soup.find_all("div", class_="BNeawe vvjwJb AP7Wnd")

    news_titles = []
    for result in news_results:
        news_titles.append(result.text)

    return news_titles
```

then the command will be:
test.py|2,1|2,1|from bs4 import BeautifulSoup

---~~~+++===+++~~~---
test.py|5,5|5,33|html = requests.get(url).text
    soup = BeautifulSoup(html, "html.parser")
    news_results = soup.find_all("div", class_="BNeawe vvjwJb AP7Wnd")
---~~~+++===+++~~~---
test.py|7,5|9,13|news_titles = []
    for result in news_results:
        news_titles
---~~~+++===+++~~~---
test.py|11,16|11,16|_titles
"""

import os
import re
from typing import Tuple

from .verify import verify


class Position:
    separator = ","

    def __init__(self, line: int, col: int):
        self.line: int = line
        self.col: int = col

    def __str__(self):
        return f"(Ln {self.line}, Col {self.col})"

    @staticmethod
    def from_str(pos: str) -> "Position":
        line, col = pos.split(Position.separator)
        return Position(int(line) - 1, int(col) - 1)


class PatchCommand:
    separator = "|"

    def __init__(self, filepath: str, start: Position, end: Position, content: str):
        self.filepath: str = filepath
        self.start: Position = start
        self.end: Position = end
        self.content: str = content

    def read_lines(self) -> list[str]:
        with open(self.filepath, "r") as f:
            lines = f.readlines()
        return lines

    def write_lines(self, lines: list[str]) -> int:
        with open(self.filepath, "w") as f:
            f.writelines(lines)
        return sum([len(line) for line in lines])

    @verify
    def execute(self) -> Tuple[int, int]:
        lines = self.read_lines()
        before = sum([len(line) for line in lines])

        lines[self.start.line] = (
            lines[self.start.line][: self.start.col]
            + self.content
            + lines[self.end.line][self.end.col :]
        )
        lines = lines[: self.start.line + 1] + lines[self.end.line + 1 :]

        after = self.write_lines(lines)

        written = len(self.content)
        deleted = before - after + written

        return written, deleted

    @staticmethod
    def from_str(command: str) -> "PatchCommand":
        match = re.search(
            r"(.*)\|([0-9]*),([0-9]*)\|([0-9]*),([0-9]*)(\||\n)(.*)",
            command,
            re.DOTALL,
        )
        filepath = match.group(1)
        start_line = match.group(2)
        start_col = match.group(3)
        end_line = match.group(4)
        end_col = match.group(5)
        content = match.group(7)
        return PatchCommand(
            filepath,
            Position.from_str(f"{start_line},{start_col}"),
            Position.from_str(f"{end_line},{end_col}"),
            content,
        )


class CodePatcher:
    separator = "\n---~~~+++===+++~~~---\n"

    @staticmethod
    def sort_commands(commands: list[PatchCommand]) -> list[PatchCommand]:
        return sorted(commands, key=lambda c: c.start.line, reverse=True)

    @staticmethod
    def patch(bulk_command: str) -> Tuple[int, int]:
        commands = [
            PatchCommand.from_str(command)
            for command in bulk_command.split(CodePatcher.separator)
            if command != ""
        ]
        commands = CodePatcher.sort_commands(commands)

        written, deleted = 0, 0
        for command in commands:
            if command:
                w, d = command.execute()
                written += w
                deleted += d
        return written, deleted


if __name__ == "__main__":
    commands = """test.py|2,1|2,1|from bs4 import BeautifulSoup

---~~~+++===+++~~~---
test.py|5,5|5,33|html = requests.get(url).text
    soup = BeautifulSoup(html, "html.parser")
    news_results = soup.find_all("div", class_="BNeawe vvjwJb AP7Wnd")
---~~~+++===+++~~~---
test.py|7,5|9,13|news_titles = []
    for result in news_results:
        news_titles
---~~~+++===+++~~~---
test.py|11,16|11,16|_titles
"""

    example = """import requests

def crawl_news(keyword):
    url = f"https://www.google.com/search?q={keyword}+news"
    response = requests.get(url)

    news = []
    for result in response:
        news.append(result.text)

    return news
"""
    testfile = "test.py"
    with open(testfile, "w") as f:
        f.write(example)

    patcher = CodePatcher()
    written, deleted = patcher.patch(commands)
    print(f"written: {written}, deleted: {deleted}")

####################### => EDITOR/PATCH.PY






###################### EDITOR// INIT.PY


class CodeEditor(BaseToolSet):
    @tool(
        name="CodeEditor.READ",
        description="Read and understand code. "
        f"Input should be filename and line number group. ex. test.py|1-10 "
        "and the output will be code. ",
    )
    def read(self, inputs: str) -> str:
        try:
            output = CodeReader.read(inputs)
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed CodeEditor.READ, Input Commands: {inputs} "
            f"Output Answer: {output}"
        )
        return output

    @tool(
        name="CodeEditor.SUMMARY",
        description="Summary code. "
        "Read the code structured into a tree. "
        "If you set specific line, it will show the code from the specific line. "
        "Input should be filename, depth, and specific line if you want. ex. test.py|2 or test.py|3|print('hello world') "
        "and the output will be list of (line number: code). ",
    )
    def summary(self, inputs: str) -> str:
        try:
            output = CodeReader.summary(inputs)
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed CodeEditor.SUMMARY, Input Commands: {inputs} "
            f"Output Answer: {output}"
        )
        return output

    @tool(
        name="CodeEditor.APPEND",
        description="Append code to the existing file. "
        "If the code is completed, use the Terminal tool to execute it, if not, append the code through the this tool. "
        "Input should be filename and code to append. "
        "Input code must be the code that should be appended, NOT whole code. "
        "ex. test.py\nprint('hello world')\n "
        "and the output will be last 3 lines.",
    )
    def append(self, inputs: str) -> str:
        try:
            code = CodeWriter.append(inputs)
            output = "Last 3 line was:\n" + "\n".join(code.split("\n")[-3:])
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed CodeEditor.APPEND, Input: {inputs} "
            f"Output Answer: {output}"
        )
        return output

    @tool(
        name="CodeEditor.WRITE",
        description="Write code to create a new tool. "
        "If the code is completed, use the Terminal tool to execute it, if not, append the code through the CodeEditor.APPEND tool. "
        "Input should be formatted like: "
        "<filename>\n<code>\n\n"
        "Here is an example: "
        "test.py\nmessage = 'hello world'\nprint(message)\n"
        "\n"
        "The output will be last 3 lines you wrote.",
    )
    def write(self, inputs: str) -> str:
        try:
            code = CodeWriter.write(inputs.lstrip())
            output = "Last 3 line was:\n" + "\n".join(code.split("\n")[-3:])
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed CodeEditor.WRITE, Input: {inputs} " f"Output Answer: {output}"
        )
        return output

    # @tool(
    #     name="CodeEditor.PATCH",
    #     description="Patch the code to correct the error if an error occurs or to improve it. "
    #     "Input is a list of patches. The patch is separated by {seperator}. ".format(
    #         seperator=CodePatcher.separator.replace("\n", "\\n")
    #     )
    #     + "Each patch has to be formatted like below.\n"
    #     "<filepath>|<start_line>,<start_col>|<end_line>,<end_col>|<new_code>"
    #     "Here is an example. If the original code is:\n"
    #     "print('hello world')\n"
    #     "and you want to change it to:\n"
    #     "print('hi corca')\n"
    #     "then the patch should be:\n"
    #     "test.py|1,8|1,19|hi corca\n"
    #     "Code between start and end will be replaced with new_code. "
    #     "The output will be written/deleted bytes or error message. ",
    # )
    def patch(self, patches: str) -> str:
        try:
            w, d = CodePatcher.patch(patches)
            output = f"successfully wrote {w}, deleted {d}"
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed CodeEditor.PATCH, Input Patch: {patches} "
            f"Output Answer: {output}"
        )
        return output

    @tool(
        name="CodeEditor.DELETE",
        description="Delete code in file for a new start. "
        "Input should be filename."
        "ex. test.py "
        "Output will be success or error message.",
    )
    def delete(self, inputs: str) -> str:
        try:
            with open(filepath, "w") as f:
                f.write("")
            output = "success"
        except Exception as e:
            output = str(e)

        logger.debug(
            f"\nProcessed CodeEditor.DELETE, Input filename: {inputs} "
            f"Output Answer: {output}"
        )
        return output
    
###################### EDITOR// INIT.PY END






########################### MODELS
import os
import uuid

import numpy as np
import torch
from diffusers import (
    EulerAncestralDiscreteScheduler,
    StableDiffusionInpaintPipeline,
    StableDiffusionInstructPix2PixPipeline,
    StableDiffusionPipeline,
)
from PIL import Image
from transformers import (
    BlipForQuestionAnswering,
    BlipProcessor,
    CLIPSegForImageSegmentation,
    CLIPSegProcessor,
)

from logger import logger
from utils import get_new_image_name


class MaskFormer(BaseToolSet):
    def __init__(self, device):
        print("Initializing MaskFormer to %s" % device)
        self.device = device
        self.processor = CLIPSegProcessor.from_pretrained("CIDAS/clipseg-rd64-refined")
        self.model = CLIPSegForImageSegmentation.from_pretrained(
            "CIDAS/clipseg-rd64-refined"
        ).to(device)

    def inference(self, image_path, text):
        threshold = 0.5
        min_area = 0.02
        padding = 20
        original_image = Image.open(image_path)
        image = original_image.resize((512, 512))
        inputs = self.processor(
            text=text, images=image, padding="max_length", return_tensors="pt"
        ).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        mask = torch.sigmoid(outputs[0]).squeeze().cpu().numpy() > threshold
        area_ratio = len(np.argwhere(mask)) / (mask.shape[0] * mask.shape[1])
        if area_ratio < min_area:
            return None
        true_indices = np.argwhere(mask)
        mask_array = np.zeros_like(mask, dtype=bool)
        for idx in true_indices:
            padded_slice = tuple(
                slice(max(0, i - padding), i + padding + 1) for i in idx
            )
            mask_array[padded_slice] = True
        visual_mask = (mask_array * 255).astype(np.uint8)
        image_mask = Image.fromarray(visual_mask)
        return image_mask.resize(original_image.size)


class ImageEditing(BaseToolSet):
    def __init__(self, device):
        print("Initializing ImageEditing to %s" % device)
        self.device = device
        self.mask_former = MaskFormer(device=self.device)
        self.revision = "fp16" if "cuda" in device else None
        self.torch_dtype = torch.float16 if "cuda" in device else torch.float32
        self.inpaint = StableDiffusionInpaintPipeline.from_pretrained(
            "runwayml/stable-diffusion-inpainting",
            revision=self.revision,
            torch_dtype=self.torch_dtype,
        ).to(device)

    @tool(
        name="Remove Something From The Photo",
        description="useful when you want to remove and object or something from the photo "
        "from its description or location. "
        "The input to this tool should be a comma separated string of two, "
        "representing the image_path and the object need to be removed. ",
    )
    def inference_remove(self, inputs):
        image_path, to_be_removed_txt = inputs.split(",")
        return self.inference_replace(f"{image_path},{to_be_removed_txt},background")

    @tool(
        name="Replace Something From The Photo",
        description="useful when you want to replace an object from the object description or "
        "location with another object from its description. "
        "The input to this tool should be a comma separated string of three, "
        "representing the image_path, the object to be replaced, the object to be replaced with ",
    )
    def inference_replace(self, inputs):
        image_path, to_be_replaced_txt, replace_with_txt = inputs.split(",")
        original_image = Image.open(image_path)
        original_size = original_image.size
        mask_image = self.mask_former.inference(image_path, to_be_replaced_txt)
        updated_image = self.inpaint(
            prompt=replace_with_txt,
            image=original_image.resize((512, 512)),
            mask_image=mask_image.resize((512, 512)),
        ).images[0]
        updated_image_path = get_new_image_name(
            image_path, func_name="replace-something"
        )
        updated_image = updated_image.resize(original_size)
        updated_image.save(updated_image_path)

        logger.debug(
            f"\nProcessed ImageEditing, Input Image: {image_path}, Replace {to_be_replaced_txt} to {replace_with_txt}, "
            f"Output Image: {updated_image_path}"
        )

        return updated_image_path


class InstructPix2Pix(BaseToolSet):
    def __init__(self, device):
        print("Initializing InstructPix2Pix to %s" % device)
        self.device = device
        self.torch_dtype = torch.float16 if "cuda" in device else torch.float32
        self.pipe = StableDiffusionInstructPix2PixPipeline.from_pretrained(
            "timbrooks/instruct-pix2pix",
            safety_checker=None,
            torch_dtype=self.torch_dtype,
        ).to(device)
        self.pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(
            self.pipe.scheduler.config
        )

    @tool(
        name="Instruct Image Using Text",
        description="useful when you want to the style of the image to be like the text. "
        "like: make it look like a painting. or make it like a robot. "
        "The input to this tool should be a comma separated string of two, "
        "representing the image_path and the text. ",
    )
    def inference(self, inputs):
        """Change style of image."""
        logger.debug("===> Starting InstructPix2Pix Inference")
        image_path, text = inputs.split(",")[0], ",".join(inputs.split(",")[1:])
        original_image = Image.open(image_path)
        image = self.pipe(
            text, image=original_image, num_inference_steps=40, image_guidance_scale=1.2
        ).images[0]
        updated_image_path = get_new_image_name(image_path, func_name="pix2pix")
        image.save(updated_image_path)

        logger.debug(
            f"\nProcessed InstructPix2Pix, Input Image: {image_path}, Instruct Text: {text}, "
            f"Output Image: {updated_image_path}"
        )

        return updated_image_path


class Text2Image(BaseToolSet):
    def __init__(self, device):
        print("Initializing Text2Image to %s" % device)
        self.device = device
        self.torch_dtype = torch.float16 if "cuda" in device else torch.float32
        self.pipe = StableDiffusionPipeline.from_pretrained(
            "runwayml/stable-diffusion-v1-5", torch_dtype=self.torch_dtype
        )
        self.pipe.to(device)
        self.a_prompt = "best quality, extremely detailed"
        self.n_prompt = (
            "longbody, lowres, bad anatomy, bad hands, missing fingers, extra digit, "
            "fewer digits, cropped, worst quality, low quality"
        )

    @tool(
        name="Generate Image From User Input Text",
        description="useful when you want to generate an image from a user input text and save it to a file. "
        "like: generate an image of an object or something, or generate an image that includes some objects. "
        "The input to this tool should be a string, representing the text used to generate image. ",
    )
    def inference(self, text):
        image_filename = os.path.join("image", str(uuid.uuid4())[0:8] + ".png")
        prompt = text + ", " + self.a_prompt
        image = self.pipe(prompt, negative_prompt=self.n_prompt).images[0]
        image.save(image_filename)

        logger.debug(
            f"\nProcessed Text2Image, Input Text: {text}, Output Image: {image_filename}"
        )

        return image_filename


class VisualQuestionAnswering(BaseToolSet):
    def __init__(self, device):
        print("Initializing VisualQuestionAnswering to %s" % device)
        self.torch_dtype = torch.float16 if "cuda" in device else torch.float32
        self.device = device
        self.processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
        self.model = BlipForQuestionAnswering.from_pretrained(
            "Salesforce/blip-vqa-base", torch_dtype=self.torch_dtype
        ).to(self.device)

    @tool(
        name="Answer Question About The Image",
        description="useful when you need an answer for a question based on an image. "
        "like: what is the background color of the last image, how many cats in this figure, what is in this figure. "
        "The input to this tool should be a comma separated string of two, representing the image_path and the question",
    )
    def inference(self, inputs):
        image_path, question = inputs.split(",")
        raw_image = Image.open(image_path).convert("RGB")
        inputs = self.processor(raw_image, question, return_tensors="pt").to(
            self.device, self.torch_dtype
        )
        out = self.model.generate(**inputs)
        answer = self.processor.decode(out[0], skip_special_tokens=True)

        logger.debug(
            f"\nProcessed VisualQuestionAnswering, Input Image: {image_path}, Input Question: {question}, "
            f"Output Answer: {answer}"
        )

        return answer
    
#segment anything:

########################### MODELS
