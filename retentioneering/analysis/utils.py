import numpy as np
import pandas as pd
import seaborn as sns
from datetime import datetime
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.cluster import KMeans, DBSCAN
from sklearn.neighbors import NearestNeighbors
import networkx as nx
import os
import json


def str_agg(x):
    return ' '.join(x)


def prepare_dataset(df, target_event, event_filter=None, n_start_events=None):
    if event_filter is not None:
        df = df[~df.event_name.isin(event_filter)]
    df = df.sort_values('event_timestamp')
    train = df.groupby('user_pseudo_id').event_name.agg(str_agg)
    train = train.reset_index(None)
    train.event_name = train.event_name.apply(lambda x: x.split())
    train['target'] = train.event_name.apply(lambda x: x[-1] == target_event)
    train.event_name = train.event_name.apply(lambda x: x[:-1])
    if n_start_events:
        train.event_name = train.event_name.apply(lambda x: ' '.join(x[:n_start_events]))
    else:
        train.event_name = train.event_name.apply(lambda x: ' '.join(x))
        return train


def get_agg(df, agg_type):
    agg = df.groupby(['event_name', 'next_event'], as_index=False)
    agg = agg['time_to_next_event'].agg(agg_type.split('_')[1])
    agg.columns = ['event_name', 'next_event', agg_type]
    return agg


def get_shift(df):
    df = df.sort_values(['user_pseudo_id', 'event_timestamp']) \
        .reset_index(drop=True)

    shift = df.groupby('user_pseudo_id').shift(-1)
    df['next_event'] = shift.event_name
    df['time_to_next_event'] = (shift.event_timestamp - df.event_timestamp)
    df = df[df.next_event.notnull()]
    return df


def get_all_agg(df, agg_list):
    if 'next_event' not in df.columns:
        df = get_shift(df)

    df_result = get_agg(df, agg_list[0])
    for agg in agg_list[1:]:
        df_result[agg] = get_agg(df, agg)[agg]
    return df_result


def get_adjacency(df, adj_type):
    df = df.copy()
    event_set = set(df.event_name.unique())
    event_set.update(df.next_event.unique())
    event_num = len(event_set)
    event_to_id = dict(zip(event_set, np.arange(event_num)))
    df.event_name = df.event_name.apply(event_to_id.get)
    df.next_event = df.next_event.apply(event_to_id.get)
    if 'count' in adj_type:
        adj = np.zeros((event_num, event_num))
    else:
        adj = -np.ones((event_num, event_num))

    for i in df.iterrows():
        adj[int(i[1].event_name), int(i[1].next_event)] = i[1][adj_type]

    names = sorted(event_to_id, key=event_to_id.get)
    adj = pd.DataFrame(adj, columns=names, index=names)
    adj = adj.round(2)
    return adj


def get_accums(agg, name, max_rank):
    """
    Creates Accumulator Variable
    :param agg: Counts of events by step
    :param name: Name of Accumulator
    :param max_rank: Number of steps in pivot
    :return: Accumulator Variable
    """
    lost = pd.DataFrame([[0, 0]], columns=['event_rank', 'freq']) \
        .append(agg.loc[agg.event_name == name, ['event_rank', 'freq']])

    lost['event_rank'] += 1
    lost = lost.sort_values('event_rank')

    missed_rows = []
    k = 0
    for i, row in enumerate(lost.itertuples()):
        for j in range(row.event_rank - (i + 1) - k):
            missed_rows.append([i + k + 1, 0])
            k += 1
    lost = lost.append(pd.DataFrame(missed_rows, columns=['event_rank', 'freq']))
    lost = lost.sort_values('event_rank')
    lost.freq = lost.freq.cumsum()
    while lost['event_rank'].max() < max_rank:
        lost = lost.append(
            pd.DataFrame([[lost['event_rank'].iloc[-1] + 1, lost.freq.iloc[-1]]], columns=['event_rank', 'freq']),
            sort=True
        )
    lost['event_name'] = 'Accumulated ' + name.capitalize()
    return lost


def check_folder(settings):
    if settings.get('export_folder'):
        return settings
    else:
        if not os.path.isdir('./experiments/'):
            os.mkdir('./experiments/')
        settings['export_folder'] = './experiments/{}'.format(datetime.now())
        os.mkdir(settings['export_folder'])
        with open(os.path.join(settings['export_folder'], 'settings_{}.json'.format(datetime.now())), 'w') as f:
            json.dump(settings, f)
    return settings


def get_desc_table(df, settings, target_event_list=list(['lost', 'passed']), max_steps=None, plot=True, plot_name=None):
    """
    Builds distribution of events over steps
    :param df: Clickstream
    :param plot: if True: plot heatmap
    :return: Pivot table with distribution of events over steps
    """
    # create ranks and count
    df = df.sort_values(['user_pseudo_id', 'event_timestamp']).copy()
    df['event_rank'] = 1
    df['event_rank'] = df.groupby('user_pseudo_id')['event_rank'].cumsum()
    if max_steps:
        df = df.loc[df['event_rank'] <= max_steps, :]

    agg = df.groupby(['event_rank', 'event_name'], as_index=False)['user_pseudo_id'].count()
    agg.columns = ['event_rank', 'event_name', 'freq']
    tot_cnt = agg[agg['event_rank'] == 1].freq.sum()

    # add accumulated rows
    max_rank = agg['event_rank'].max() + 1
    for i in target_event_list:
        agg = agg.append(get_accums(agg, i, max_rank), sort=True)

    # build pivot
    agg['freq'] = agg['freq'] / tot_cnt
    piv = agg.pivot(index='event_name', columns='event_rank', values='freq').fillna(0)
    piv.columns.name = None
    piv.index.name = None
    piv = piv.round(2)

    if max_steps:
        piv = piv.T[piv.columns <= max_steps].T

    if plot:
        # create folder for experiment if doesn't exists
        settings = check_folder(settings)
        export_folder = settings['export_folder']
        sns.mpl.pyplot.figure(figsize=(20, 10))
        heatmap = sns.heatmap(piv, annot=True, cmap="YlGnBu")
        if plot_name:
            filename = os.path.join(export_folder, 'desc_table_{}.png'.format(plot_name))
        else:
            filename = os.path.join(export_folder, 'desc_table_{}.png'.format(datetime.now()))
        heatmap.get_figure().savefig(filename)

    return piv


def get_diff(df_old, df_new, settings, precalc=False, plot=True, plot_name=None):
    """
    Gets difference between two groups
    :param df_old: Raw clickstream or calculated desc table of last version
    :param df_new:  Raw clickstream or calculated desc table of new version
    :param precalc: If True: use precalculated desc tables
    :param plot: If False: plot heatmap
    :return: Table of differences between two versions
    """
    if precalc:
        desc_new = df_new
        desc_old = df_old
    else:
        desc_old = get_desc_table(df_old, False)
        desc_new = get_desc_table(df_new, False)

    old_id = set(desc_old.index)
    new_id = set(desc_new.index)

    if old_id != new_id:
        for idx in new_id - old_id:
            row = pd.Series([0] * desc_old.shape[1], name=idx)
            row.index += 1
            desc_old = desc_old.append(row, sort=True)
        for idx in old_id - new_id:
            row = pd.Series([0] * desc_new.shape[1], name=idx)
            row.index += 1
            desc_new = desc_new.append(row, sort=True)

    max_old = desc_old.shape[1]
    max_new = desc_new.shape[1]
    if max_old < max_new:
        for i in range(max_old, max_new + 1):
            desc_old[i] = desc_old[i - 1]
    elif max_old > max_new:
        for i in range(max_new, max_old + 1):
            desc_new[i] = desc_new[i - 1]

    diff = desc_new - desc_old
    diff = diff.sort_index(axis=1)
    if plot:
        settings = check_folder(settings)
        export_folder = settings['export_folder']

        sns.mpl.pyplot.figure(figsize=(20, 10))
        heatmap = sns.heatmap(diff, annot=True, cmap="YlGnBu")
        if plot_name:
            filename = os.path.join(export_folder, 'desc_table_{}.png'.format(plot_name))
        else:
            filename = os.path.join(export_folder, 'desc_table_{}.png'.format(datetime.now()))
        heatmap.get_figure().savefig(filename)
    return diff


def plot_graph_python(df_agg, agg_type, settings, plot_name=None):
    edges = df_agg.loc[:, ['event_name', 'next_event', agg_type]]
    G = nx.DiGraph()
    G.add_weighted_edges_from(edges.values)
    width = [G.get_edge_data(i, j)['weight'] for i, j in G.edges()]
    width = np.array(width)
    width = (width - width.min()) / (np.mean(width) - width.min())
    width *= 2
    width = np.where(width > 20, 20, width)
    width = np.where(width < 2, 2, width)

    pos = nx.random_layout(G, seed=2)
    f = sns.mpl.pyplot.figure(figsize=(20, 10))
    nx.draw_networkx_edges(G, pos, edge_color='b', alpha=0.2, width=width);
    nx.draw_networkx_nodes(G, pos, node_color='b', alpha=0.3)
    pos = {k:[pos[k][0], pos[k][1] + 0.03] for k in pos.keys()}
    nx.draw_networkx_labels(G, pos, node_color='b', font_size=16);
    sns.mpl.pyplot.axis('off')

    settings = check_folder(settings)
    export_folder = settings['export_folder']
    if plot_name:
        filename = os.path.join(export_folder, 'graphvis_{}.png'.format(plot_name))
    else:
        filename = os.path.join(export_folder, 'graphvis_{}.png'.format(datetime.now()))
    f.savefig(filename)


def plot_frequency_map(df, settings, target_events=['lost', 'passed'], plot_name=None):
    users = df.user_pseudo_id[df.event_name.isin(target_events)].unique()
    df = df[df.user_pseudo_id.isin(users)]
    data = prepare_dataset(df, '')
    cv = CountVectorizer()
    x = cv.fit_transform(data.event_name.values).todense()
    cols = cv.inverse_transform(np.ones(df.event_name.nunique() - len(target_events)))[0]
    x = pd.DataFrame(x, columns=cols, index=data.user_pseudo_id)
    nodes_hist = df.groupby('event_name',
                            as_index=False).event_timestamp.count().sort_values('event_timestamp',
                                                                                ascending=False)
    nodes_hist.event_name = nodes_hist.event_name.apply(lambda x: x.lower())
    sorted_cols = nodes_hist.event_name[~nodes_hist.event_name.isin(target_events)].values
    x = x.loc[:, sorted_cols]
    sns.mpl.pyplot.figure(figsize=[8, 5])
    bar = sns.barplot(nodes_hist.event_name.values, nodes_hist.event_timestamp.values, palette='YlGnBu')
    bar.set_xticklabels(bar.get_xticklabels(), rotation=90);

    settings = check_folder(settings)
    export_folder = settings['export_folder']
    if plot_name:
        barname = os.path.join(export_folder, 'bar_{}.png'.format(plot_name))
        heatname = os.path.join(export_folder, 'countmap_{}.png'.format(plot_name))
    else:
        barname = os.path.join(export_folder, 'bar_{}.png'.format(datetime.now()))
        heatname = os.path.join(export_folder, 'countmap_{}.png'.format(datetime.now()))
    bar.get_figure().savefig(barname)
    sns.mpl.pyplot.figure(figsize=[10, 15])
    heatmap = sns.heatmap(x.values, cmap="YlGnBu")
    heatmap.get_figure().savefig(heatname)
    return x


def plot_clusters(data, countmap, target_events=['lost', 'passed'], n_clusters=None, plot_cnt=2, width=10, height=5):
    if n_clusters:
        clusterer = KMeans(n_clusters=n_clusters)
    else:
        nn = NearestNeighbors(metric='cosine')
        nn.fit(countmap.values)
        dists = nn.kneighbors(countmap.values, 2)[0][:, 1]
        eps = np.percentile(dists, 99)
        clusterer = DBSCAN(eps=eps, metric='cosine')
    clusters = clusterer.fit_predict(countmap)
    cl = pd.DataFrame(clusters, columns=['cluster'])
    cl['c'] = 1
    main_classes = cl.groupby('cluster',
                              as_index=False).count().sort_values('c',
                                                                  ascending=False).cluster.iloc[:plot_cnt].values
    groups = []
    for i in main_classes:
        groups.append(countmap.index[clusters == i].values)
    sizes = []
    for group in groups:
        tmp = data[data.user_pseudo_id.isin(group)]
        sz = []
        for event in target_events:
            sz.append(tmp[tmp.event_name == event].user_pseudo_id.nunique())
        sizes.append(sz)

    fig, ax = sns.mpl.pyplot.subplots(1 if plot_cnt <= 2 else (plot_cnt // 2 + 1), 2)
    fig.set_size_inches(width, height)
    for i, j in enumerate(sizes):
        if plot_cnt <= 2:
            ax[i].pie(j, labels=['lost', 'passed'], autopct='%1.1f%%')
            ax[i].set_title('Class {}'.format(i))
        else:
            ax[i // 2][i % 2].pie(j, labels=['lost', 'passed'], autopct='%1.1f%%')
            ax[i // 2][i % 2].set_title('Class {}'.format(i))


def filter_welcome(df):
    passed = df[df.event_name.apply(lambda x: str(x).split('_')[0] in [u'newFlight', u'feed', u'tabbar', u'myFlights'])]
    myfligts = df[(df.event_name == 'screen_view') & (df.event_params_value_string_value == 'myFlights')]
    myfligts = myfligts.append(passed, ignore_index=False, sort=False)
    myfligts = myfligts.sort_values(['user_pseudo_id', 'event_timestamp'])
    passed = myfligts.groupby('user_pseudo_id').head(1)
    passed.event_name = 'passed'

    lost = df[~df.user_pseudo_id.isin(passed.user_pseudo_id)].groupby('user_pseudo_id').tail(1)
    lost.event_name = 'lost'
    lost.event_timestamp -= 1
    df = df.append(lost.append(passed, sort=False), sort=False)
    return prepare_prunned(df)


def prepare_prunned(df):
    welcome2event = {
        '1': "wel",
        '2': "push",
        '3': "sub",
        '4': "wallet",
        '5': "import",
        '6': "cal"
    }
    rename_dict = {
        'inactive_users': 'inactive',
        'new_users': 'newUsers',
        'lost': 'lostUser'
    }
    wel = df[df['event_name'] == 'welcome_see_screen'].copy()
    wel.event_name = wel.event_params_value_string_value.apply(welcome2event.get)
    wel = wel[wel.event_name.notnull()]
    wel = wel.append(df[df.event_name.isin(['lost', 'passed'])], ignore_index=True, sort=False)
    wel = wel.sort_values(['user_pseudo_id', 'event_timestamp']).reset_index(drop=True)
    first = wel.drop_duplicates('user_pseudo_id', keep='first')
    users = first.user_pseudo_id[first.event_name.isin(['wel', 'passed'])].values
    first = first[first.user_pseudo_id.isin(users)]
    wel = wel[wel.user_pseudo_id.isin(users)]
    first.event_name = 'new_users'
    first.event_timestamp -= 1
    wel = wel.append(first, ignore_index=True, sort=False)
    for i in set(wel.event_name.unique()):
        if i not in rename_dict:
            rename_dict.update({i: i})
    wel.event_name = wel.event_name.apply(rename_dict.get)
    return wel
