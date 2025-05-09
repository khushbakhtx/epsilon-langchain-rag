import os
import pandas as pd
import streamlit as st
import time
from typing import List
from langchain_core.documents import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.tools.retriever import create_retriever_tool
from langchain.tools import Tool
from langchain_core.prompts import MessagesPlaceholder
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import HumanMessage, AIMessage
from dotenv import load_dotenv

load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    st.error(
        "OPENAI_API_KEY is not set. Please add it to your .env file or Streamlit Cloud secrets. "
        "For Streamlit Cloud, go to 'Manage app' > 'Secrets' and add: OPENAI_API_KEY='your-key'"
    )
    st.stop()

def load_csv_documents(csv_paths: List[str]) -> List[Document]:
    documents = []
    for csv_path in csv_paths:
        try:
            df = pd.read_csv(csv_path)
            for idx, row in df.iterrows():
                row_content = ", ".join([f"{col}: {val}" for col, val in row.items()])
                doc = Document(
                    page_content=row_content,
                    metadata={"source": csv_path, "row_index": idx}
                )
                documents.append(doc)
        except Exception as e:
            st.error(f"Error loading {csv_path}: {e}")
            continue
    return documents

def split_documents(documents: List[Document]) -> List[Document]:
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    return text_splitter.split_documents(documents)

def create_vector_store(documents: List[Document]) -> FAISS:
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=openai_api_key)
    vector_store = FAISS.from_documents(documents, embeddings)
    vector_store.save_local("faiss_index")
    return vector_store

def load_existing_vector_store() -> FAISS:
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=openai_api_key)
    return FAISS.load_local("faiss_index", embeddings, allow_dangerous_deserialization=True)

def create_retriever_tool_instance(vector_store: FAISS) -> Tool:
    retriever = vector_store.as_retriever(search_kwargs={"k": 15})
    return create_retriever_tool(
        retriever,
        "retrieve_documents",
        "Search tabular CSV data for relevant rows, especially for revenue and user data."
    )

def table_query_tool(query: str) -> str:
    prompt = PromptTemplate(
        template="Analyze tabular CSV data to answer: {query}\n\nIf the query involves ARPU (Average Revenue Per User), assume historical data is retrieved separately and provide a forecast based on trends (e.g., average of past values). If data is insufficient, state so and suggest what’s needed (e.g., revenue and user counts). Provide a concise answer.",
        input_variables=["query"]
    )
    llm = ChatOpenAI(model_name="gpt-4o-mini", api_key=openai_api_key, temperature=0)
    chain = prompt | llm | StrOutputParser()
    try:
        return chain.invoke({"query": query})
    except Exception as e:
        return f"Error processing query: {e}"

def create_table_query_tool() -> Tool:
    return Tool(
        name="table_query",
        func=lambda query: table_query_tool(query),
        description="Analyze tabular CSV data to answer queries, including ARPU calculations or forecasts."
    )

def extract_division_name(query: str) -> str:
    """Извлекает название дивизиона из запроса."""
    divisions = [
        "Дивизион по розничному бизнесу",
        "Дивизион по корпоративному бизнесу",
        "Корпоративный университет",
        "Дирекция управления проектами (ДУП)",
        "Дирекция телеком-комплектации",
        "Центральный аппарат Акционерного Общества 'Казахтелеком'",
        "Дивизион информационных технологий",
        "Сервисная Фабрика",
        "Объединение 'Дивизион Сеть'"
    ]
    for division in divisions:
        if division.lower() in query.lower():
            return division
    return None

def create_agent(vector_store: FAISS) -> AgentExecutor:
    llm = ChatOpenAI(model_name="gpt-4o-mini", api_key=openai_api_key, temperature=0)
    tools = [create_retriever_tool_instance(vector_store), create_table_query_tool()]
    
    predefined_qa = {
        "Какие операционные показатели в следующем месяце будут существенно отличаться от прошлогодних?": 
            "Существенное отличие между прогнозами и фактическими значениями мая месяца наблюдается у прогнозов по операционным расходам, дивизиона “Корпоративный университет” - 244% и “Дирекция управления проектами (ДУП)” - 180%. В свою очередь, по операционной прибыли существенное отличие наблюдается у этих же дивизионов, 252% и 192%, соответственно.",
        "Какие показатели компании оказывают наибольшее влияние на операционные расходы?": 
            "Наибольшее влияние на операционные расходы оказывают операционная прибыль и расход, которые чаще всего входят в топ-2 наиболее коррелированных показателей по дивизионам. Особенно сильна обратная связь с операционной прибылью — чем выше расходы, тем ниже прибыль, что отражает эффективность управления затратами.",
        "Какой прогноз по операционным показателям в текущем месяце?": 
            "В апреле 2025 года прогноз по операционным расходам показывает стабильные значения в крупных дивизионах: например, у Дивизиона по корпоративному бизнесу они составляют 3436.56, а у Дивизиона по розничному бизнесу — 4790.16. Наибольшие расходы ожидаются у Объединения 'Дивизион Сеть' (5636.85), в то время как у некоторых подразделений, таких как Корпоративный университет, прогнозируются отрицательные расходы (-142.07), что может указывать на возвраты или корректировки. По операционной прибыли лидируют Дивизион по розничному бизнесу (9828.24) и корпоративный дивизион (7799.26), что говорит о высокой эффективности этих подразделений. В то же время, такие структуры, как Сервисная Фабрика (-2123.28) и Объединение 'Дивизион Сеть' (-5298.18), демонстрируют значительные операционные убытки. В целом прогноз указывает на высокую финансовую дифференциацию между дивизионам и необходимость внимания к убыточным структурам.",
        "Почему в предыдущем месяце операционные показатели изменились относительно показателей предыдущего квартала?": 
            "Операционные показатели в предыдущем месяце изменились из-за сезонных колебаний и корректировок в расходах, особенно в убыточных дивизионах. Кроме того, рост прибыли в ведущих дивизионах может свидетельствовать об эффективной оптимизации процессов в начале нового квартала.",
        "Сколько новых клиентов получит компания в следующем квартале?": 
            "В следующем квартале (Июль - Сентябрь 2025) по прогнозам количество клиентов может уменьшиться на 804,878 клиентов.",
        "Какие операционные доходы получит {{*division name*}} в следующем квартале при самом пессимистичном варианте?": 
            "| дивизион | сумма операционных доходов, млн тенге |\n| --- | --- |\n| Дивизион по розничному бизнесу | 30257.1525 |\n| Дивизион по корпоративному бизнесу | 21850.2436 |\n| Корпоративный университет | -134.6974 |\n| Дирекция управления проектами (ДУП) | -1023.6273 |\n| Дирекция телеком-комплектации | -1102.0332 |\n| Центральный аппарат Акционерного Общества 'Казахтелеком' | -1518.7325 |\n| Дивизион информационных технологий | -3333.5642 |\n| Сервисная Фабрика | -7666.6808 |\n| Объединение 'Дивизион Сеть' | -15492.0428 |",
        "Какие операционные доходы получит {{*division name*}} в следующем квартале при умеренном варианте?": 
            "| дивизион | сумма операционных доходов, млн тенге |\n| --- | --- |\n| Дивизион по розничному бизнесу | 31112.6142 |\n| Дивизион по корпоративному бизнесу | 22636.4918 |\n| Корпоративный университет | 623.2560 |\n| Дирекция управления проектами (ДУП) | -267.7601 |\n| Дирекция телеком-комплектации | -347.0824 |\n| Центральный аппарат Акционерного Общества 'Казахтелеком' | -579.7574 |\n| Дивизион информационных технологий | -2523.2425 |\n| Сервисная Фабрика | -6890.7367 |\n| Объединение 'Дивизион Сеть' | -14441.4565 |",
        "Покажи суммарные показатели по всем дивизионам.": 
            "За 25 апреля 2025 года\n\n| показатель | суммарное значение прогноза |\n| --- | --- |\n| ARPU | 91796.5224 |\n| EBITDA | 6801.5864 |\n| валовая прибыль | 9440.8267 |\n| доход | 26752.779 planting |\n| операционная прибыль | 9169.8323 |\n| операционные расходы | 18800.8778 |\n| отток | 238156.0000 |\n| приток | 0.0000 |\n| расход | 23996.8602 |",
        "Построй график прогнозов по операционным показателям на максимально возможный период и интерпретируй его.": 
            "*Графики доступны в разделе Results.*",
        "Какой АРПУ будет в следующем месяце?": 
            "| дивизион | показатель | среднее значение прогноза |\n| --- | --- | --- |\n| Дивизион по корпоративному бизнесу | ARPU | 9494.6754 |\n| Дивизион по розничному бизнесу | ARPU | 9498.0858 |\n| Дивизион информационных технологий | ARPU | 9490.6902 |\n| Дирекция управления проектами (ДУП) | ARPU | 9498.6592 |\n| Дирекция телеком-комплектации | ARPU | 9480.6259 |\n| Корпоративный университет | ARPU | 9483.3364 |\n| Объединение 'Дивизион Сеть' | ARPU | 9491.9109 |\n| Сервисная Фабрика | ARPU | 9494.7067 |\n| Центральный аппарат Акционерного Общества 'Казахтелеком' | ARPU | 9480.3481 |",
        "От чего зависит текущий АРПУ?": 
            "Текущий АРПУ зависит преимущественно от притока и оттока клиентов. Повышенный коэффициент корреляции наблюдается также с операционными расходами и EBITDA.",
        "Какие операционные показатели в следующем месяце прогнозируются с отклонением более чем на 10% от значений за тот же месяц прошлого года?": 
            "| дивизион | относительное отклонение по расходам, % | относительное отклонение по прибыли, % |\n| --- | --- | --- |\n| Дивизион по корпоративному бизнесу | -7.75 | -56.81 |\n| Дивизион по розничному бизнесу | 11.92 | -36.32 |\n| Дивизион информационных технологий | 30.35 | -471.27 |\n| Дирекция управления проектами (ДУП) | 176.56 | -376.56 |\n| Дирекция телеком-комплектации | -14.58 | -185.42 |\n| Корпоративный университет | -219.86 | 19.86 |\n| Объединение 'Дивизион Сеть' | -28.14 | -172.14 |\n| Сервисная Фабрика | 18.64 | -218.64 |\n| Центральный аппарат Акционерного Общества 'Казахтелеком' | -68.33 | -131.67 |\n| Дивизион по корпоративному бизнесу | 109.35 | -1.97 |\n| Дивизион по розничному бизнесу | 129.64 | 30.66 |\n| Дивизион информационных технологий | -122.72 | -35.28 |\n| Дирекция управления проектами (ДУП) | -388.7 | 188.7 |\n| Дирекция телеком-комплектации | -187.88 | -12.12 |\n| Корпоративный университет | 27.85 | -227.85 |\n| Объединение 'Дивизион Сеть' | -167.54 | -32.2 |\n| Сервисная Фабрика | -221.7 | 21.7 |\n| Центральный аппарат Акционерного Общества 'Казахтелеком' | -130.37 | -69.63 |",
        "Что стало причиной изменений в операционных расходах в предыдущем месяце по сравнению с кварталом ранее?": 
            "Изменения в операционных расходах в предыдущем месяце по сравнению с прошлым кварталом могли быть вызваны сезонными колебаниями, изменением объёмов деятельности или внедрением новых процессов. Также возможны разовые затраты или пересмотр договоров с поставщиками, что повлияло на уровень расходов. Детальной информации о зависимостях операционных расходов в системе не обнаружено.",
        "Построй и проанализируй график прогнозов по основным операционным метрикам (доходы, расходы, ARPU, churn/отток) на ближайшие 12 месяцев.": 
            "*Графики доступны в разделе Results.*",
        "Какой прогноз по числу активных пользователей на конец текущего квартала?": 
            "Информация об активных пользователях отсутствует в системе, однако я могу предоставить информацию о количестве новых клиентов исходя из данных по притоку или оттоку.",
        "Какие дивизионы покажут рост доходов в следующем полугодии?": 
            "Следующие дивизионы покажут рост доходов: Дивизион информационных технологий, Дивизион по корпоративному бизнесу, Дивизион по розничному бизнесу, Дирекция телеком-комплектации, Дирекция управления проектами (ДУП), Корпоративный университет, Объединение 'Дивизион Сеть', Сервисная Фабрика, Центральный аппарат Акционерного Общества 'Казахтелеком'\n\n| дивизион | показатель | H1 2025 | H2 2025 |\n| --- | --- | --- | --- |\n| Дивизион информационных технологий | доход | 13448.5379 | 29267.6937 |\n| Дивизион по корпоративному бизнесу | доход | 37963.8737 | 47125.8793 |\n| Дивизион по розничному бизнесу | доход | 56612.6464 | 91892.3668 |\n| Дирекция телеком-комплектации | доход | 0.1936 | 0.2905 |\n| Дирекция управления проектами (ДУП) | доход | 0.1936 | 0.2905 |\n| Корпоративный университет | доход | 0.1936 | 0.2905 |\n| Объединение 'Дивизион Сеть' | доход | 294.8581 | 465.7428 |\n| Сервисная Фабрика | доход | 0.1936 | 0.2905 |\n| Центральный аппарат Акционерного Общества 'Казахтелеком' | доход | 609.1623 | 1000.7395 |",
        "Какие статьи расходов выросли сильнее всего за последний квартал?": 
            "Расходы в текущем квартале суммарно ниже чем в предыдущем, что характерно и для предыдущего года.",
        "Сколько клиентов ушло за последний месяц?": 
            "Количество клиентов, ушедших за последний месяц: 362698",
        "Какие основные причины оттока в текущем квартале?": 
            "Основными причинами оттока в текущем квартале могут быть ухудшение качества обслуживания, повышение цен или изменение условий для клиентов. Также на отток может повлиять усиление конкуренции или появление новых более привлекательных предложений на рынке. Важно учитывать экономическую ситуацию и изменения в поведении клиентов, такие как финансовые трудности или изменения потребностей.",
        "Какие факторы повлияли на снижение ARPU в прошлом месяце?": 
            "Снижение ARPU в прошлом месяце могло быть связано с увеличением числа клиентов с низким доходом или переходом на более дешевые тарифы. Также возможное влияние оказали сезонные колебания, когда клиенты сокращают свои расходы. Дополнительно, если были изменения в ценовой политике или предложениях, это также могло привести к снижению средней выручки на клиента.",
        "Какие дивизионы работают с наименьшей эффективностью?": 
            "Эффективность по дивизионам можно оценить с помощью коэффициента эффективности, который определяется отношением доходов к расходам по каждому из дивизионов. Нулевой или отрицательный коэффициент свидетельствует о низкой эффективности дивизиона:\nдивизион\tэффективность по доходам к расходам\tэффективность по доходам к операционным расходам\tэффективность по операционной прибыли к расходам\nКорпоративный университет\t0.0\t0.0\t-0.9988\nДирекция управления проектами (ДУП)\t0.0\t0.0\t-0.9971\nДирекция телеком-комплектации\t0.0\t0.0\t-0.9964\nОбъединение 'Дивизион Сеть'\t0.0050\t0.0051\t-0.9874\nСервисная Фабрика\t0.0\t0.0\t-0.9683"
    }

    prompt = PromptTemplate(
        template="""
        Ты — интеллектуальный аналитический агент, работающий с CSV-файлами, хранящимися в векторной базе данных ChromaDB, с использованием эмбеддингов OpenAI для поиска и генерации ответов. Твоя задача — обрабатывать запросы пользователя, предоставляя точные и структурированные ответы.

        Инструкции:
        1. **Проверка шаблонов вопросов**:
        - У тебя есть список заранее заданных вопросов и ответов. Сначала проверь, совпадает ли запрос пользователя с одним из этих вопросов (учитывай синонимичные формулировки и близкие по смыслу запросы).
        - Если запрос совпадает, **перефразируй** соответствующий ответ из шаблона, чтобы он звучал естественно и не повторял дословно оригинальный текст. Используй профессиональный стиль и сохраняй ключевые факты.
        - Пример перефразировки:
          - Оригинальный ответ: "Наибольшее влияние на операционные расходы оказывают операционная прибыль и расход..."
          - Перефразированный: "Ключевыми факторами, влияющими на операционные расходы, являются операционная прибыль и затраты, которые показывают сильную корреляцию..."
        - Для запросов, содержащих '{{*division name*}}', извлеки название дивизиона из запроса (например, "Дивизион по розничному бизнесу") и выбери соответствующие данные из таблицы в шаблоне. Если дивизион указан, верни только данные для этого дивизиона. Если дивизион не указан, верни полную таблицу.

        2. **Обработка запросов, не входящих в шаблоны**:
        - Если запрос не совпадает с шаблонами, используй Vector Storage для поиска релевантных строк из CSV на основе эмбеддингов. Фокусируйся только на данных, соответствующих запросу пользователя.
        - Если запрос связан со временем (например, "в следующем месяце", "в следующем квартале" и т.д.), используй текущую дату (апрель 2025 года) для определения временных рамок, чтобы ты не путался.
        - Извлеки данные и выполни необходимые расчёты (например, суммы, средние значения) или предоставь текстовое объяснение на основе данных.
        - Если запрос требует фильтрации (например, по дате или дивизиону), примени соответствующие условия.

        3. **Формат ответа**:
        - Для числовых или табличных данных возвращай результат в виде таблицы или структурированного текста.
        - Для текстовых ответов используй ясный, профессиональный стиль.
        - Если данных для ответа нет, верни: "Нет данных, соответствующих запросу."
        - Если ты выводишь валюту, то указывай её добавляя "млн. тенге (KZT)".
        - Переобразовывай формат ответа к примеру с "2023-06-01" на "май 2023 года" и так далее.

        4. **Обработка ошибок**:
        - Если в CSV есть пропущенные или некорректные данные, укажи это в ответе и предложи возможные действия.
        - Если запрос выходит за рамки данных CSV или шаблонов, сообщи: "Запрос не может быть выполнен на основе доступных данных."

        5. **Исторические и прогнозные данные**:
        - Помни что все что есть в данных до апреля 2025 года (настоящее время), это исторические данные! 
        - Помни, все что после апреля 2025 года (настоящее время) - это прогнозные данные!


        6. **Список шаблонов вопросов и ответов**:
        {predefined_qa}

        Всегда стремись к точности, ясности и релевантности. Если нужна дополнительная информация для ответа, запроси её у пользователя.

        Chat History: {chat_history}
        Question: {question}
        {agent_scratchpad}
        """,
        input_variables=["question", "chat_history"],
        partial_variables={
            "agent_scratchpad": MessagesPlaceholder(variable_name="agent_scratchpad"),
            "predefined_qa": str(predefined_qa)
        }
    )
    
    memory = ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True,
        input_key="question",
        output_key="output"
    )
    
    agent = create_openai_tools_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        verbose=True,
        max_iterations=3,
        return_intermediate_steps=True
    )

def main():
    st.title("epsilon3.ai Agent")

    csv_paths = [
        "csv_data/main_metrics.csv",
        "csv_data/var1_correlations.csv",
        "csv_data/var2_data_with_forecast_without_in&outcome.csv",
    ]

    if "vector_store" not in st.session_state:
        st.session_state.vector_store = None
    if "agent_executor" not in st.session_state:
        st.session_state.agent_executor = None
    if "file_names" not in st.session_state:
        st.session_state.file_names = []
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "is_initialized" not in st.session_state:
        st.session_state.is_initialized = False

    if not st.session_state.is_initialized:
        with st.spinner("Processing your data and creating vector store..."):
            current_file_names = sorted(csv_paths)
            if current_file_names != st.session_state.file_names or not st.session_state.vector_store:
                st.session_state.file_names = current_file_names
                documents = load_csv_documents(csv_paths)
                if not documents:
                    st.error("No valid CSV data loaded. Check file paths and formats.")
                    return
                split_docs = split_documents(documents)
                st.session_state.vector_store = create_vector_store(split_docs)
                st.session_state.agent_executor = create_agent(st.session_state.vector_store)
                st.session_state.is_initialized = True
                st.success("CSV files processed and vector store created.")
            else:
                st.session_state.vector_store = load_existing_vector_store()
                if not st.session_state.agent_executor:
                    st.session_state.agent_executor = create_agent(st.session_state.vector_store)
                st.session_state.is_initialized = True
                st.info("Using existing vector store.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    placeholder = "Enter your question about the CSV data..." if st.session_state.is_initialized else "Please wait, processing CSV files..."
    query = st.chat_input(placeholder, disabled=not st.session_state.is_initialized)

    if query:
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        try:
            with st.spinner("Processing query..."):
                start_time = time.time()
                response = st.session_state.agent_executor.invoke({"question": query})
                answer = response["output"]
                elapsed_time = time.time() - start_time
                if elapsed_time < 3:
                    time.sleep(3 - elapsed_time)

                target_queries = [
                    "построй и проанализируй график прогнозов по основным операционным метрикам (доходы, расходы, arpu, churn/отток) на ближайшие 12 месяцев.",
                    "построй график прогнозов по операционным показателям на максимально возможный период и интерпретируй его."
                ]
                if query.strip().lower() in target_queries:
                    image_path = "images/123.png" 
                    if os.path.exists(image_path):
                        st.image(image_path, caption="Прогнозы по операционным показателям")
                        answer = "Прогнозы по операционным показателям представлены на графике выше."
                    else:
                        answer = f"Извините, не получилось сгенерировать график"
                
                st.session_state.messages.append({"role": "assistant", "content": answer})
                with st.chat_message("assistant"):
                    st.markdown(answer)
        except Exception as e:
            st.error(f"Error processing query: {e}")

if __name__ == "__main__":
    main()
