<<<<<<< HEAD
from swarms.structs import Agent
=======
from swarms.structs import Flow
>>>>>>> 3d3dddaf0c7894ec2df14c51f7dd843c41c878c4
from swarms.models.gpt4_vision_api import GPT4VisionAPI
from swarms.prompts.multi_modal_autonomous_instruction_prompt import (
    MULTI_MODAL_AUTO_AGENT_SYSTEM_PROMPT_1,
)

llm = GPT4VisionAPI()

task = (
    "Analyze this image of an assembly line and identify any issues such as"
    " misaligned parts, defects, or deviations from the standard assembly"
    " process. IF there is anything unsafe in the image, explain why it is"
    " unsafe and how it could be improved."
)
img = "assembly_line.jpg"

## Initialize the workflow
<<<<<<< HEAD
flow = Agent(
=======
flow = Flow(
>>>>>>> 3d3dddaf0c7894ec2df14c51f7dd843c41c878c4
    llm=llm,
    max_loops=1,
    dashboard=True,
)

flow.run(task=task, img=img)
