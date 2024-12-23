from EventManager.Models.RunnerEvents import RunnerEvents
from EventManager.EventSubscriptionController import EventSubscriptionController
from ConfigValidator.Config.Models.RunTableModel import RunTableModel
from ConfigValidator.Config.Models.FactorModel import FactorModel
from ConfigValidator.Config.Models.RunnerContext import RunnerContext
from ConfigValidator.Config.Models.OperationType import OperationType
from ExtendedTyping.Typing import SupportsStr
from ProgressManager.Output.OutputProcedure import OutputProcedure as output

from typing import Dict, List, Any, Optional
from pathlib import Path
from os.path import dirname, realpath

import paramiko
import pandas as pd
from scp import SCPClient
from os import getenv
from dotenv import load_dotenv
from evaluate import load as load_evaluation
import time
load_dotenv()

class ExternalMachineAPI:
    def __init__(self):

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        self.stdin = None
        self.stdout = None
        self.stderr = None
        
        try:
            self.ssh.connect(hostname=getenv('HOSTNAME'), username=getenv('EXPERIMENTAL_MACHINE_USER'), password=getenv('PASSWORD'))
        except paramiko.SSHException:
            print('Failed to send run command to machine!')

    def execute_remote_command(self, command : str = '', overwrite_channels : bool = True):
        try:
            # Execute the command
            if overwrite_channels:
                self.stdin, self.stdout, self.stderr = self.ssh.exec_command(command)
            else:
                self.ssh.exec_command(command)
        except paramiko.SSHException:
            print('Failed to send command to machine!')
        except TimeoutError:
            print('Timeout reached while waiting for command output.')

    def copy_file_from_remote(self, remote_path, local_path):
        # Create SSH client and SCP client
        with SCPClient(self.ssh.get_transport()) as scp:
            # Copy the file from remote to local
            scp.get(remote_path, local_path, recursive=True)
        print(f"Copied {remote_path} to {local_path}")

    def __del__(self):
        if self.stdin:
            self.stdin.close()
        if self.stdout:
            self.stdout.close()
        if self.stderr:
            self.stderr.close()
        self.ssh.close()

def parse_energibridge_output(file_path):
    # Define target columns
    target_columns = [
        'GPU0_MEMORY_USED', 'GPU0_USAGE', 'USED_MEMORY', 'USED_SWAP',
    ] + [f'CPU_USAGE_{i}' for i in range(32)]

    delta_target_columns = [
        'DRAM_ENERGY (J)', 'PACKAGE_ENERGY (J)', 'PP0_ENERGY (J)', 'PP1_ENERGY (J)', 'GPU0_ENERGY (mJ)'
    ]

    # Read the file into a pandas DataFrame
    df = pd.read_csv(file_path).apply(pd.to_numeric, errors='coerce')

    # Calculate column-wise averages, ignoring NaN values and deltas from start of experiment to finish
    averages = df[target_columns].mean().to_dict()
    deltas = {column : df[column].iloc[-1] - df[column].iloc[0]  for column in delta_target_columns}

    return dict(averages.items() | deltas.items())

def score_inference_output(score_type : str, inference_output : str, expected_outputs : List[str]):
    evaluation = load_evaluation(score_type)
    scores = evaluation.compute(predictions=[inference_output], references=[expected_outputs])

    score = next(iter(scores.values()))

    if score <= 0.4:
        output.console_log_FAIL(f"Performance ({score_type}) Score: {score:.4f}")
    elif 0.4 < score <= 0.8:
        output.console_log_bold(f"Performance ({score_type}) Score: {score:.4f}")
    else:
        output.console_log_OK(f"Performance ({score_type}) Score: {score:.4f}")

    return scores

class RunnerConfig:
    ROOT_DIR = Path(dirname(realpath(__file__)))

    # ================================ USER SPECIFIC CONFIG ================================
    """The name of the experiment."""
    name:                       str             = "inference_experiment"

    """The path in which Experiment Runner will create a folder with the name `self.name`, in order to store the
    results from this experiment. (Path does not need to exist - it will be created if necessary.)
    Output path defaults to the config file's path, inside the folder 'experiments'"""
    results_output_path:        Path            = ROOT_DIR / 'experiments'

    """Experiment operation type. Unless you manually want to initiate each run, use `OperationType.AUTO`."""
    operation_type:             OperationType   = OperationType.AUTO

    """The time Experiment Runner will wait after a run completes.
    This can be essential to accommodate for cooldown periods on some systems."""
    time_between_runs_in_ms:    int             = 30000

    # Dynamic configurations can be one-time satisfied here before the program takes the config as-is
    # e.g. Setting some variable based on some criteria
    def __init__(self):
        """Executes immediately after program start, on config load"""
        EventSubscriptionController.subscribe_to_multiple_events([
            (RunnerEvents.BEFORE_EXPERIMENT, self.before_experiment),
            (RunnerEvents.BEFORE_RUN       , self.before_run       ),
            (RunnerEvents.START_RUN        , self.start_run        ),
            (RunnerEvents.START_MEASUREMENT, self.start_measurement),
            (RunnerEvents.INTERACT         , self.interact         ),
            (RunnerEvents.STOP_MEASUREMENT , self.stop_measurement ),
            (RunnerEvents.STOP_RUN         , self.stop_run         ),
            (RunnerEvents.POPULATE_RUN_DATA, self.populate_run_data),
            (RunnerEvents.AFTER_EXPERIMENT , self.after_experiment )
        ])
        self.run_table_model = None  # Initialized later
        self.project_name = 'greenlab-course-project'
        self.input_prompts = {
            "generation": {
                "short": {
                    "instruction": "Generate a coherent and contextually appropriate completion for the sentence.",
                    "content": "Artificial intelligence has transformed industries by improving...",
                    "output_length": 100,
                    "expected_outputs": [
                        "Artificial intelligence has transformed industries by improving efficiency in operations, reducing human error, and automating repetitive tasks. For example, in manufacturing, AI-powered robotics streamline assembly lines, ensuring precision and consistency. In logistics, predictive analytics optimize supply chains by forecasting demand and reducing delays. Healthcare benefits from AI algorithms capable of analyzing medical data quickly, aiding in early diagnosis and personalized treatments. Financial institutions use AI to detect fraudulent activities and provide tailored investment advice. By reducing waste and streamlining processes, AI has allowed businesses to focus on innovation, enhancing their capacity to meet consumer demands and stay competitive in dynamic markets.",
                        "Artificial intelligence has transformed industries by improving decision-making capabilities through advanced data analysis. Machine learning algorithms uncover patterns in large datasets that were previously inaccessible to humans. In marketing, AI predicts consumer preferences, enabling personalized campaigns that boost engagement. Retailers use it to analyze purchasing behavior, ensuring better inventory management. In agriculture, AI monitors weather patterns and soil conditions, guiding farmers toward optimal planting decisions. By synthesizing complex information into actionable insights, AI empowers businesses to make strategic choices, enhancing productivity and profitability. This shift from reactive to proactive management has revolutionized how industries operate, innovate, and deliver value.",
                        "Artificial intelligence has transformed industries by improving customer experiences through personalized interactions and services. Chatbots powered by natural language processing (NLP) handle queries efficiently, providing real-time assistance. Recommendation systems in e-commerce and streaming platforms tailor suggestions to individual preferences, increasing user satisfaction. In the hospitality industry, AI enhances guest experiences by anticipating needs, such as automated room controls or personalized travel itineraries. AI-driven sentiment analysis helps businesses gauge customer feedback and adjust their strategies. This customization fosters stronger customer relationships, turning one-time users into loyal patrons. Ultimately, AI's ability to create meaningful, individualized experiences has reshaped consumer expectations across sectors.",
                        "Artificial intelligence has transformed industries by improving quality control through automated monitoring systems. In manufacturing, computer vision inspects products with unparalleled precision, identifying defects that might escape human oversight. Pharmaceutical companies leverage AI to ensure compliance with safety standards during drug production. The automotive industry employs AI in predictive maintenance, minimizing breakdowns and extending machinery lifespan. AI’s ability to monitor, detect, and predict errors reduces waste and ensures consistent output. By embedding these systems into production lines, industries can maintain higher standards, avoid costly recalls, and build consumer trust, cementing AI's role as a key driver of excellence.",
                        "Artificial intelligence has transformed industries by improving accessibility to services, making technology more inclusive. In education, AI supports personalized learning platforms that adapt to individual needs, assisting students with varying abilities. Healthcare benefits from AI-enabled diagnostic tools in remote areas, bridging gaps in medical expertise. Assistive technologies, such as speech-to-text applications, empower individuals with disabilities to communicate and participate in the workforce. Smart cities integrate AI to provide accessible public services, like real-time transit updates and adaptive traffic management systems. By breaking barriers to essential services, AI fosters equity and inclusivity, reshaping societal infrastructure for greater accessibility.",
                        "Artificial intelligence has transformed industries by improving sustainability efforts, enabling smarter resource management. AI-driven systems optimize energy usage in smart buildings, reducing consumption and environmental impact. In agriculture, precision farming techniques powered by AI minimize water usage and fertilizer application, increasing crop yield while conserving resources. Retailers employ AI to manage inventory efficiently, cutting down on overproduction and waste. Environmental monitoring systems utilize AI to analyze climate data, predict natural disasters, and guide conservation efforts. By driving eco-friendly innovations, AI helps industries align with global sustainability goals, demonstrating that technological progress and environmental stewardship can go hand in hand.",
                        "Artificial intelligence has transformed industries by improving risk assessment and mitigation. In finance, AI models predict market fluctuations, enabling investors to make informed decisions. Insurance companies use AI to assess claims more accurately and detect fraudulent activities. In cybersecurity, AI identifies potential threats and vulnerabilities in real-time, ensuring proactive defense measures. AI’s predictive analytics also help supply chains mitigate risks from disruptions, such as geopolitical tensions or natural disasters. By identifying potential risks before they escalate, industries can safeguard operations and ensure resilience. This proactive approach reduces uncertainty, fostering confidence among stakeholders and ensuring long-term stability.",
                        "Artificial intelligence has transformed industries by improving workforce collaboration and productivity. AI-powered tools, such as intelligent virtual assistants, streamline workflows by automating meeting scheduling, task prioritization, and follow-ups. Collaboration platforms with embedded AI features enhance remote team interactions through real-time translation and transcription. In creative fields, AI accelerates content generation, freeing professionals to focus on innovation. For researchers, AI accelerates data analysis, allowing quicker hypothesis testing. This integration of AI into daily operations not only reduces operational inefficiencies but also enhances employee satisfaction by removing mundane tasks. Consequently, organizations experience heightened productivity and innovation, redefining traditional work environments.",
                        "Artificial intelligence has transformed industries by improving safety standards in high-risk environments. In construction, AI-powered drones survey sites to identify potential hazards, reducing on-site accidents. The oil and gas industry employs predictive maintenance systems that prevent equipment failures, safeguarding workers. Autonomous vehicles use AI to navigate safely, decreasing road traffic incidents. In aviation, AI enhances air traffic control, ensuring smoother operations and reduced collision risks. AI also monitors workplace environments, such as detecting harmful gas leaks in factories. By leveraging AI for safety, industries protect their workforce and infrastructure, showcasing the transformative potential of technology in risk-laden sectors.",
                        "Artificial intelligence has transformed industries by improving innovation cycles, accelerating the development of new products and services. In pharmaceuticals, AI reduces drug discovery timelines by analyzing molecular structures and predicting efficacy. Automotive companies use AI to design more efficient engines and develop autonomous vehicles. Fashion brands incorporate AI to analyze trends, expediting the creation of seasonal collections. AI-generated simulations help engineers test prototypes without costly physical iterations. By optimizing research and development processes, AI not only reduces costs but also shortens time-to-market. This has catalyzed a new era of rapid innovation, enabling industries to meet consumer demands more swiftly."
                    ]
                },
                "long": {
                    "instruction": "Expand upon the given paragraph with logical, evidence-based details or related concepts.",
                    "content": "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change.",
                    "output_length": 300,
                    "expected_outputs": [
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. In response to climate change, significant economic and technological advancements have transformed global efforts to combat its impacts. Economically, there has been a rapid rise in investments in renewable energy. Wind, solar, and hydropower industries have grown exponentially, supported by subsidies, tax incentives, and international funding mechanisms. Carbon trading markets, established under initiatives like the European Union Emissions Trading System (EU ETS), have provided financial incentives for companies to reduce emissions. These markets promote innovation by putting a monetary value on carbon reductions, driving private sector engagement in sustainable practices. Technologically, breakthroughs in energy storage have addressed the intermittency of renewable sources. Advancements in battery technologies, such as lithium-ion and emerging solid-state batteries, have enabled more reliable energy grids. Smart grid systems integrate these technologies with AI to optimize energy distribution and reduce waste. Carbon capture, utilization, and storage (CCUS) technologies have also advanced, capturing emissions from industrial processes and even directly from the air, offering potential for negative emissions. The rise of green technology has further spurred innovation in sectors such as transportation, with electric vehicles (EVs) becoming mainstream. Companies like Tesla, along with traditional automakers, have pioneered EV technology, supported by widespread charging infrastructure expansion. These economic and technological strides are central to achieving global climate targets and mitigating the legacy of the Industrial Revolution.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. Efforts to tackle climate change have driven substantial economic and technological progress. Economically, the renewable energy sector has seen unprecedented growth, becoming a major contributor to global GDP. Investments in solar and wind energy, backed by international policies and incentives, have resulted in significant cost reductions, making these sources competitive with fossil fuels. Green bonds have emerged as a powerful financial tool, channeling billions into sustainable infrastructure projects, from renewable energy plants to urban green spaces. Technological innovation has been a cornerstone of these advancements. Solar photovoltaic efficiency has improved dramatically, enabling greater energy output at lower costs. Similarly, wind turbines have grown in scale and efficiency, with offshore wind farms capitalizing on high wind speeds to produce record levels of energy. The development of smart cities, powered by IoT devices, allows for real-time monitoring of energy usage, optimizing consumption and minimizing waste. Additionally, advancements in agricultural technology, such as precision farming and vertical farming, have reduced the carbon footprint of food production. These innovations minimize resource use while increasing yields, addressing both climate and food security concerns. Together, these economic and technological strides demonstrate a global commitment to addressing the environmental consequences of industrialization.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. The global response to climate change has catalyzed significant economic and technological advancements. Economically, governments and private sectors have mobilized vast resources toward decarbonization. Renewable energy investments have skyrocketed, with solar and wind energy leading the way. Emerging economies, once reliant on coal, are transitioning to green energy sources, aided by international funding and technology transfer programs. Furthermore, carbon pricing mechanisms, such as cap-and-trade systems and carbon taxes, incentivize industries to innovate and reduce emissions. Technologically, green advancements have reshaped energy production and consumption. Solar panels have achieved remarkable efficiency gains, while battery storage solutions have improved grid reliability. Carbon capture and utilization technologies are now integrated into major industrial processes, reducing net emissions from critical sectors. The transportation industry has also undergone a transformation, with electric vehicles and hydrogen fuel cells reducing reliance on fossil fuels.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. Economic and technological advancements in response to climate change have redefined industries and fostered innovation. On the economic front, renewable energy investments have driven a global shift away from fossil fuels. Countries have diversified energy portfolios, with solar and wind becoming cost-competitive alternatives. Green finance initiatives, including carbon credits and environmental bonds, have supported these transitions by providing funding for sustainable projects. Circular economy principles have also emerged, encouraging industries to reduce waste and maximize resource efficiency. Technological progress has played a central role. Advances in solar panel efficiency and wind turbine design have revolutionized renewable energy production. Energy storage systems, such as grid-scale batteries, mitigate the variability of renewable sources. Smart grids integrate renewable energy into national power systems, optimizing consumption and reducing carbon footprints. Furthermore, breakthroughs in carbon capture and sequestration technologies aim to offset emissions from hard-to-decarbonize industries like cement and steel production.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. The global fight against climate change has stimulated economic growth in sustainable industries and prompted unprecedented technological innovation. Economically, renewable energy sectors have become major job creators, with solar and wind energy leading the way. Governments have introduced subsidies and tax incentives to encourage green investments, while private corporations are increasingly committing to net-zero goals, driving further innovation. On the technological side, innovations in renewable energy storage have reduced dependence on fossil fuels. Wind turbines now operate in offshore environments with enhanced efficiency, and solar panels are being developed to capture energy even in low-light conditions. Additionally, AI-driven optimization tools are helping industries minimize waste and maximize energy use.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. Efforts to address climate change have fostered significant economic and technological advancements. The emergence of green technologies has redefined energy landscapes, with renewables like solar and wind achieving record-low costs. International agreements have catalyzed economic frameworks, such as carbon pricing, which incentivize reductions in emissions. The growth of green bonds has also provided funding for climate resilience projects. Technologically, innovations such as hydrogen fuel cells are being deployed for industrial and transportation applications. Additionally, advancements in material sciences have enabled the creation of energy-efficient buildings and sustainable construction practices. These developments represent a shift toward a future where technology and economic policy converge to address environmental challenges.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. Addressing climate change has driven progress in green economic policies and transformative technologies. Economically, carbon markets have created financial incentives for reducing emissions. Governments have launched green stimulus packages, promoting renewable energy projects and low-carbon infrastructure. Industries are adopting circular economy models, repurposing waste into resources, thus minimizing environmental impact. Technological innovations have led to breakthroughs in renewable energy efficiency and deployment. Offshore wind farms and concentrated solar power systems are expanding global clean energy capacity. Additionally, carbon-neutral fuels, such as biofuels and green hydrogen, are being scaled for industrial and transportation use. These advancements reflect a collective move toward a sustainable future.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. The fight against climate change has led to groundbreaking economic and technological initiatives. Renewable energy sources now dominate energy investments, with countries racing to achieve carbon neutrality. Clean energy innovation has reduced dependence on coal and oil, while carbon pricing schemes drive emissions reductions. Technological solutions like AI-powered energy management systems optimize resource use, lowering overall emissions. Electric vehicles are now mainstream, supported by improvements in battery storage. Additionally, carbon capture technologies aim to neutralize emissions from traditional industries. Together, these advancements demonstrate the global commitment to reversing the environmental consequences of industrialization.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. Climate action has fostered innovation in both economic strategies and technological development. Green financing tools, including carbon credits and renewable energy funds, have allowed nations to accelerate decarbonization. Sustainable agriculture practices supported by technology are reducing methane and other greenhouse gases. Technological advancements, particularly in AI and machine learning, have enhanced efficiency in renewable energy. Autonomous drones and sensors aid in environmental monitoring, enabling better conservation strategies. Furthermore, advancements in sustainable urban planning, like passive cooling systems, reflect a shift toward net-zero living environments. These combined efforts are addressing climate change while reshaping economies and technology sectors.",
                        "The Industrial Revolution marked a pivotal moment in human history, with profound impacts on economies, societies, and the environment. One of the lasting consequences of this era is the rise in greenhouse gas emissions, contributing to global warming. Over the years, various international efforts, such as the Kyoto Protocol and the Paris Agreement, have aimed to address this issue. Continuing this discussion, provide a summary of the economic and technological advancements that have emerged as part of the response to climate change. Global efforts to combat climate change have resulted in substantial economic shifts and innovative technologies. Economically, nations are investing in renewable energy sectors, creating jobs and fostering innovation. Green subsidies and carbon taxes incentivize industries to transition away from fossil fuels. On the technological front, advancements in solar panel efficiency and wind turbine technology have made renewable energy more accessible. Energy-efficient technologies, such as LED lighting and smart home systems, reduce consumption at the consumer level. Large-scale initiatives, such as carbon capture projects, provide solutions for emissions-heavy industries. Together, these efforts signify a new era of sustainable progress."
                    ]
                }
            },
            "question_answering": {
                "short": {
                    "instruction": "Provide a precise answer to the following factual question.",
                    "content": "What are the capitals of all european countries?",
                    "output_length": 300,
                    "expected_outputs": [
                        "The capitals of European countries are: Andorra - Andorra la Vella, Albania - Tirana, Austria - Vienna, Belarus - Minsk, Belgium - Brussels, Bosnia and Herzegovina - Sarajevo, Bulgaria - Sofia, Croatia - Zagreb, Cyprus - Nicosia, Czechia - Prague, Denmark - Copenhagen, Estonia - Tallinn, Finland - Helsinki, France - Paris. Georgia - Tbilisi, Germany - Berlin, Greece - Athens, Hungary - Budapest, Iceland - Reykjavik, Ireland - Dublin, Italy - Rome, Kosovo - Pristina, Latvia - Riga, Liechtenstein - Vaduz, Lithuania - Vilnius, Luxembourg - Luxembourg, Malta - Valletta, Moldova - Chisinau, Monaco - Monaco. Montenegro - Podgorica, Netherlands - Amsterdam, North Macedonia - Skopje, Norway - Oslo, Poland - Warsaw, Portugal - Lisbon, Romania - Bucharest, Russia - Moscow, San Marino - San Marino, Serbia - Belgrade, Slovakia - Bratislava, Slovenia - Ljubljana, Spain - Madrid, Sweden - Stockholm, Switzerland - Bern, Ukraine - Kyiv, UK - London, Vatican - Vatican City.",
                        "Here are the capitals of all European countries: Albania: Tirana Andorra: Andorra la Vella Austria: Vienna Belarus: Minsk Belgium: Brussels Bosnia and Herzegovina: Sarajevo Bulgaria: Sofia Croatia: Zagreb Cyprus: Nicosia Czech Republic: Prague Denmark: Copenhagen Estonia: Tallinn Finland: Helsinki France: Paris Germany: Berlin Greece: Athens Hungary: Budapest Iceland: Reykjavik Ireland: Dublin Italy: Rome Kosovo: Pristina Latvia: Riga Liechtenstein: Vaduz Lithuania: Vilnius Luxembourg: Luxembourg Malta: Valletta Moldova: Chișinău Monaco: Monaco Montenegro: Podgorica Netherlands: Amsterdam North Macedonia: Skopje Norway: Oslo Poland: Warsaw Portugal: Lisbon Romania: Bucharest San Marino: San Marino Serbia: Belgrade Slovakia: Bratislava Slovenia: Ljubljana Spain: Madrid Sweden: Stockholm Switzerland: Bern Turkey: Ankara (partly European) Ukraine: Kyiv United Kingdom: London Vatican City: Vatican City This includes all recognized European countries and their capitals.",

                    ]
                },
                "long": {
                    "instruction": "Analyze the provided context to generate an accurate and well-structured answer.",
                    "content": "Climate change is driven by the accumulation of greenhouse gases in the atmosphere, with carbon dioxide being the most significant contributor due to fossil fuel combustion. Other gases like methane and nitrous oxide also play substantial roles. What are the primary sources of these emissions, and how do they vary across different industries?",
                    "output_length": 250,
                    "expected_outputs": [
                        "The primary sources of greenhouse gas (GHG) emissions vary across industries, with carbon dioxide (CO₂), methane (CH₄), and nitrous oxide (N₂O) originating from specific activities linked to human activity. Carbon dioxide (CO₂) emissions, the largest contributor to climate change, are predominantly from fossil fuel combustion for energy and transportation. Power generation using coal, oil, and natural gas accounts for the highest share, followed by industrial processes like cement production and deforestation. The transportation sector, including road vehicles, aviation, and shipping, is another significant source. Methane (CH₄), a potent but shorter-lived greenhouse gas, primarily comes from agriculture, waste, and energy sectors. Livestock farming, particularly enteric fermentation in cattle, is the leading source. Rice paddies and agricultural practices also release CH₄. Landfills and wastewater treatment contribute methane emissions through anaerobic decomposition of organic matter. The energy sector adds to methane emissions via leaks during natural gas extraction, processing, and distribution. Nitrous oxide (N₂O) emissions stem mostly from agricultural practices. The overuse of synthetic fertilizers releases nitrogen into the soil, which undergoes microbial processes to emit N₂O. Other sources include industrial activities, wastewater treatment, and combustion of fossil fuels and biomass. Industries differ in their emission profiles. Agriculture is the largest source of methane and nitrous oxide, while energy and transportation dominate CO₂ emissions. Industrial processes contribute a mix, especially from cement and chemical production. Understanding these variations is crucial for designing sector-specific mitigation strategies to reduce emissions and combat climate change.",
                        "Greenhouse gas (GHG) emissions, including carbon dioxide (CO₂), methane (CH₄), and nitrous oxide (N₂O), arise from a range of activities across various industries, with each gas playing a distinct role depending on the source. Carbon dioxide (CO₂): CO₂ is the primary greenhouse gas emitted by human activities, mainly from the combustion of fossil fuels such as coal, oil, and natural gas. The energy sector, responsible for electricity and heat production, accounts for about 40 percent of CO₂ emissions globally. Transportation is another major contributor, driven by the reliance on gasoline and diesel. Industrial activities, such as cement and chemical production, release CO₂ both through energy consumption and specific chemical processes. Methane (CH₄): Methane, with a significantly higher warming potential than CO₂ over the short term, is primarily emitted from agriculture, waste, and energy sectors. In agriculture, livestock digestion (enteric fermentation) and manure management are the dominant sources. Landfills and wastewater treatment facilities produce methane as organic material breaks down without oxygen. Additionally, fossil fuel extraction activities, including natural gas production and coal mining, result in methane leaks. Nitrous oxide (N₂O): Nitrous oxide, with its much greater warming potential compared to CO₂, is largely linked to agricultural practices. The excessive use of nitrogen-based fertilizers and mismanaged manure releases N₂O through soil processes. Smaller contributions come from industrial processes like nitric acid production and fuel combustion. Emissions vary by region, with agriculture dominating in developing nations and energy and transportation leading in industrialized regions. Tailored mitigation efforts are essential for addressing sector-specific sources effectively."
                    ]
                }
            },
            "summarization": {
                "short": {
                    "instruction": "Summarize the main points from the following brief article.",
                    "output_length": 50,
                    "content": "The adoption of renewable energy sources has been a cornerstone of global strategies to combat climate change. Solar and wind power have seen remarkable growth due to technological advancements and decreasing costs. However, the intermittency of these sources poses a challenge for energy systems, necessitating the development of energy storage technologies and grid integration strategies. Policymakers have implemented incentives, such as tax credits and feed-in tariffs, to accelerate the transition. Nevertheless, achieving carbon neutrality will require a holistic approach, incorporating energy efficiency, sustainable infrastructure development, and international collaboration.",
                    "expected_outputs": [
                        "The growth of solar and wind energy, driven by innovation and cost reduction, is vital for combating climate change. Challenges include intermittency, requiring energy storage and grid solutions. Policymakers support the transition with incentives, but carbon neutrality demands energy efficiency, sustainable infrastructure, and global collaboration.",
                        "Renewable energy, particularly solar and wind, is critical for climate strategies but faces intermittency issues needing storage and integration solutions. Policies like tax credits encourage adoption, yet achieving carbon neutrality requires a holistic focus on energy efficiency, sustainable development, and international cooperation.",
                        "Advances in solar and wind energy are key to fighting climate change, though intermittency challenges require better storage and grids. Incentives from policymakers drive progress, but carbon neutrality needs comprehensive efforts, combining efficiency, sustainable infrastructure, and global teamwork.",
                        "The rise of solar and wind power highlights renewables' role in climate solutions, but their variability calls for advanced storage and grid strategies. Incentives accelerate progress, yet carbon neutrality necessitates energy efficiency, sustainable infrastructure, and worldwide collaboration.",
                        "Renewable energy's expansion, led by solar and wind, tackles climate change but faces grid and storage challenges. Policymaker incentives support adoption, while carbon neutrality hinges on energy efficiency, sustainable infrastructure, and global partnerships."
                    ]
                },
                "long": {
                    "instruction": "Provide a concise summary of the key insights from the provided technical paper.",
                    "content": "Artificial intelligence (AI), in its broadest sense, is intelligence exhibited by machines, particularly computer systems.  It is a field of research in computer science that develops and studies methods and software that enable machines to perceive their environment and use learning and intelligence to take actions that maximize their chances of achieving defined goals. Such machines may be called AIs. Some high-profile applications of AI include advanced web search engines (e.g., Google Search); recommendation systems (used by YouTube, Amazon, and Netflix); interacting via human speech (e.g., Google Assistant, Siri, and Alexa); autonomous vehicles (e.g., Waymo); generative and creative tools (e.g., ChatGPT, and AI art); and superhuman play and analysis in strategy games (e.g., chess and Go). However, many AI applications are not perceived as AI: A lot of cutting edge AI has filtered into general applications, often without being called AI because once something becomes useful enough and common enough its not labeled AI anymore. The various subfields of AI research are centered around particular goals and the use of particular tools. The traditional goals of AI research include reasoning, knowledge representation, planning, learning, natural language processing, perception, and support for robotics. General intelligence—the ability to complete any task performable by a human on an at least equal level—is among the fields long-term goals. To reach these goals, AI researchers have adapted and integrated a wide range of techniques, including search and mathematical optimization, formal logic, artificial neural networks, and methods based on statistics, operations research, and economics. AI also draws upon psychology, linguistics, philosophy, neuroscience, and other fields. Artificial intelligence was founded as an academic discipline in 1956, and the field went through multiple cycles of optimism, followed by periods of disappointment and loss of funding, known as AI winter. Funding and interest vastly increased after 2012 when deep learning outperformed previous AI techniques. This growth accelerated further after 2017 with the transformer architecture, and by the early 2020s hundreds of billions of dollars were being invested in AI (known as the AI boom). The widespread use of AI in the 21st century exposed several unintended consequences and harms in the present and raised concerns about its risks and long-term effects in the future, prompting discussions about regulatory policies to ensure the safety and benefits of the technology.",
                    "output_length": 150,
                    "expected_outputs": [
                        "Artificial intelligence (AI) involves machines performing tasks that mimic human intelligence, using techniques like neural networks and optimization. Key applications include search engines, recommendation systems, autonomous vehicles, and generative tools. AI research spans fields such as natural language processing, robotics, and general intelligence, blending methods from mathematics, psychology, and neuroscience. Founded in 1956, AI progressed through cycles of growth and 'AI winters,' with a surge post-2012 driven by deep learning and the transformer architecture. This boom raised investments and societal impacts but also highlighted unintended consequences, sparking regulatory debates to ensure AI's safe development and use.",
                        "AI is the field of enabling machines to perceive, learn, and act intelligently, with applications in areas like search engines, speech assistants, and autonomous vehicles. Its foundations in 1956 led to cycles of innovation and setbacks, but breakthroughs in deep learning (2012) and transformer models (2017) ushered in an 'AI boom.' Research focuses on reasoning, robotics, and natural language processing, borrowing from disciplines like psychology and economics. Despite transformative uses, AI's rapid adoption has brought risks and unintended harms, prompting ongoing discussions on policies to regulate and maximize its societal benefits.",
                        "Artificial intelligence (AI) empowers machines to perform tasks requiring intelligence, integrating methods like neural networks and optimization. Established in 1956, AI evolved through phases of growth and setbacks, culminating in significant advances in deep learning and transformer models after 2012. Applications range from web searches and recommendation systems to autonomous vehicles and generative tools. The field encompasses diverse goals, including robotics and general intelligence, drawing on psychology, philosophy, and more. While AI revolutionizes industries, its widespread adoption has revealed risks and unintended consequences, prompting debates about regulatory frameworks for its safe and ethical development.",
                        "Artificial intelligence (AI) enables machines to perform tasks requiring intelligence, such as perception, learning, and decision-making. Since its foundation in 1956, AI has evolved through cycles of innovation and stagnation, with deep learning (2012) and transformer models (2017) driving its recent 'AI boom'. Applications include search engines, recommendation systems, and autonomous vehicles. AI research addresses goals like natural language processing and robotics, incorporating methods from diverse disciplines. However, its rapid integration into society has raised concerns about risks and unintended harms, leading to ongoing discussions about regulations to ensure its ethical and beneficial use.",
                        "AI, founded as a discipline in 1956, enables machines to mimic human intelligence through methods like neural networks and optimization. It underpins applications such as virtual assistants, autonomous vehicles, and generative tools. Research spans areas like learning, reasoning, and robotics, blending insights from psychology, economics, and neuroscience. After cycles of stagnation, breakthroughs in deep learning (2012) and transformer models (2017) spurred massive investments and innovation. However, the technology’s rapid deployment has highlighted risks and unintended consequences, sparking debates on regulatory frameworks to ensure responsible development and societal benefit."
                    ]
                }
            }
        }
        self.models = ["llama2", "llama3", "llama3.1", "mistral:v0.1", "mistral:v0.2", "mistral:v0.3", "qwen:7b", "qwen2", "qwen2.5", "phi", "phi3", "phi3.5", "gemma", "gemma2"]
        
        self.metric_capturing_interval  : int   = 200 # Miliseconds

        self.gpu_clock : int = 600 #Mhz
        self.gpu_power_cap : int = 100 #Watts

        output.console_log("Custom config loaded")

    def create_run_table_model(self) -> RunTableModel:
        """Create and return the run_table model here. A run_table is a List (rows) of tuples (columns),
        representing each run performed"""
        main_factor = FactorModel("model_version", self.models)
        blocking_factor_1 = FactorModel("task_type", ['generation', 'question_answering', 'summarization'])
        co_factor = FactorModel("input_size", ['short', 'long'])
        self.run_table_model = RunTableModel(
            factors=[main_factor, blocking_factor_1, co_factor],
            shuffle=True,
            repetitions=20,
            data_columns=[
                'GPU0_MEMORY_USED', 'GPU0_USAGE', 'USED_MEMORY', 'USED_SWAP',
                'DRAM_ENERGY (J)', 'PACKAGE_ENERGY (J)', 'PP0_ENERGY (J)', 'PP1_ENERGY (J)', 'GPU0_ENERGY (mJ)',
                ] + [f'CPU_USAGE_{i}' for i in range(32)] + ['rouge_scores', 'bleu_scores', 'inference_time']
        )
        return self.run_table_model

    def before_experiment(self) -> None:
        """Perform any activity required before starting the experiment here
        Invoked only once during the lifetime of the program."""
        output.console_log("Config.before_experiment() called!")
        ssh = ExternalMachineAPI()
        
        output.console_log(f'Setting up GPU frequency at {self.gpu_clock}Mhz and maximum power draw at {self.gpu_power_cap}W...')
        # Set persistence 
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo -S nvidia-smi -i 0 -pm 1")
        output.console_log(ssh.stdout.readline())
        # Set GPU frequency during usage
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo -S nvidia-smi -i 0 -lgc {self.gpu_clock}")
        output.console_log(ssh.stdout.readline())
        # Set GPU maximum power draw
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo -S nvidia-smi -i 0 -pl {self.gpu_power_cap}")
        output.console_log(ssh.stdout.readline())
        output.console_log_OK(f'GPU configuration completed!')

        output.console_log('Installing models...')
        ssh.execute_remote_command(f"./{self.project_name}/install_models.sh {','.join(self.models)}")
        machine_output = ''
        while 'Model installation process completed!' not in machine_output:
            machine_output = ssh.stdout.readline()
            output.console_log(f'Installation: {machine_output}...')
        output.console_log_OK('Model installation process completed!')

    def before_run(self) -> None:
        """Perform any activity required before starting a run.
        No context is available here as the run is not yet active (BEFORE RUN)"""
        output.console_log("Config.before_run() called!")
        self.inference_time = 0
        self.inference_output = ''
        

    def start_run(self, context: RunnerContext) -> None:
        """Perform any activity required for starting the run here.
        For example, starting the target system to measure.
        Activities after starting the run should also be performed here."""
        output.console_log("Config.start_run() called!")
        ssh = ExternalMachineAPI()
        

        # Make directory of run on experimental machine
        self.external_run_dir = f'./{self.project_name}/experiments/{self.name}/{context.run_dir.name}'
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo -S mkdir -p {self.external_run_dir}")
        output.console_log(f'Run directory on experimental machine: {self.external_run_dir}')

        # Loading the model of the run
        ssh.execute_remote_command(f"echo Respond with LOADED | ollama run {context.run_variation['model_version']}")
        output.console_log_bold(f"{ssh.stdout.readline().strip()} model: {context.run_variation['model_version']}")

    def start_measurement(self, context: RunnerContext) -> None:
        """Perform any activity required for starting measurements."""
        output.console_log("Config.start_measurement() called!")

        # Run the energibridge command in the background
        ssh = ExternalMachineAPI()
        energibridge_path = f'./{self.project_name}/EnergiBridge/target/release/energibridge'
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo -S {energibridge_path} -g --interval {self.metric_capturing_interval} --output {self.external_run_dir}/energibridge.csv sleep 600 & echo $!")
        self.energibridge_pid = int(ssh.stdout.readline())

        output.console_log(f"Energibridge collection for inference started...")

    def interact(self, context: RunnerContext) -> None:
        """Perform any interaction with the running target system here, or block here until the target finishes."""
        output.console_log("Config.interact() called!")

        input_size = context.run_variation['input_size']
        task_type = context.run_variation['task_type']

        output.console_log(f"Running inference for a {task_type} task with {input_size} input...")        
        prompting_data = self.input_prompts[task_type][input_size]
        maximum_output_prompt = f"You must respond in {prompting_data['output_length']} word(s)."

        prompt = prompting_data['instruction'] + prompting_data['content'] + maximum_output_prompt
        output.console_log(prompt)
        ssh = ExternalMachineAPI()
        # Running inference task
        start_time = time.time()
        ssh.execute_remote_command(f'echo "{prompt}" | ollama run {context.run_variation["model_version"]}')
        raw_output = ssh.stdout.readlines()
        self.inference_time = time.time() - start_time
        self.inference_output = ''.join(raw_output)
        output.console_log(self.inference_output)
        output.console_log_OK(f"Inference finished in {self.inference_time}s")


    def stop_measurement(self, context: RunnerContext) -> None:
        """Perform any activity here required for stopping measurements."""
        output.console_log("Config.stop_measurement called!")
        ssh = ExternalMachineAPI()

        # Stop energibridge
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo -S kill {self.energibridge_pid}")
        output.console_log("Energibridge collection stopped.")

    def stop_run(self, context: RunnerContext) -> None:
        """Perform any activity here required for stopping the run.
        Activities after stopping the run should also be performed here."""
        output.console_log("Config.stop_run() called!")
        ssh = ExternalMachineAPI()

        # Stop current model
        output.console_log_bold('Stopping run model...')
        ssh.execute_remote_command(f"ollama stop {context.run_variation['model_version']}")

    def populate_run_data(self, context: RunnerContext) -> Optional[Dict[str, SupportsStr]]:
        """Parse and process any measurement data here.
        You can also store the raw measurement data under `context.run_dir`
        Returns a dictionary with keys `self.run_table_model.data_columns` and their values populated"""
        output.console_log("Config.populate_run_data() called!")
        ssh = ExternalMachineAPI()
        ssh.copy_file_from_remote(f'{self.external_run_dir}/energibridge.csv', context.run_dir)

        # Store output in a file
        with open(f"{context.run_dir}/output.txt", "w") as file:
            file.write(self.inference_output)

        task_type = context.run_variation['task_type']
        expected_outputs = self.input_prompts[task_type][context.run_variation['input_size']]['expected_outputs']

        bleu_scores = score_inference_output('bleu', self.inference_output, expected_outputs)
        rouge_scores = score_inference_output('rouge', self.inference_output, expected_outputs)
        
        run_data = parse_energibridge_output(f'{context.run_dir}/energibridge.csv')
        run_data['inference_time'] = self.inference_time
        run_data['rouge_scores'] = rouge_scores
        run_data['bleu_scores'] = bleu_scores

        return run_data

    def after_experiment(self) -> None:
        """Perform any activity required after stopping the experiment here
        Invoked only once during the lifetime of the program."""

        ssh = ExternalMachineAPI()
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo nvidia-smi -i 0 -rgc")
        ssh.execute_remote_command(f"echo {getenv('PASSWORD')} | sudo nvidia-smi -pl 200")

        output.console_log("Config.after_experiment() called!")

    # ================================ DO NOT ALTER BELOW THIS LINE ================================
    experiment_path:            Path             = None
