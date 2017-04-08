import tensorflow as tf
import numpy as np
from model import l2_similarity, dot, trans, ident_entity, max_margin, skipgram_loss, ranking_error_triples
import pickle


class TransH(object):
    def __init__(self, num_entities, num_relations, embedding_size, batch_size_kg, batch_size_sg, num_sampled,
                 vocab_size, sub_prop_constr=None, init_lr=1.0, skipgram=True, lambd=None):
        """
        Implements translation-based triplet scoring from negative sampling (TransH)
        :param num_entities:
        :param num_relations:
        :param embedding_size:
        :param batch_size_kg:
        :param batch_size_sg:
        :param num_sampled:
        :param vocab_size:
        """
        self.num_entities = num_entities
        self.num_relations = num_relations
        self.embedding_size = embedding_size
        self.vocab_size = vocab_size
        self.num_sampled = num_sampled
        self.batch_size_kg = batch_size_kg
        self.batch_size_sg = batch_size_sg
        self.sub_prop_constr = sub_prop_constr
        self.init_lr = init_lr
        self.skipgram = skipgram
        self.lambd = lambd

    def rank_left_idx(self, test_inpr, test_o, test_w, ent_embs, cache=True):
        lhs = ent_embs # [num_entities, d]
        rell = test_o  # [num_test, d]
        rhs = ent_embs[test_inpr]  # [num_test, d]
        wr = test_w
        if cache:
            cache_rhs = {}
            cache_result = {}
        result = np.zeros((rhs.shape[0], lhs.shape[0]), dtype=np.float16)
        for i in xrange(rhs.shape[0]):
            if cache:
                if test_inpr[i] in cache_rhs:
                    proj_rhs = cache_rhs[test_inpr[i]]
                else:
                    proj_rhs = rhs[i] - np.dot(rhs[i], np.transpose(wr[i])) * wr[i]
                    cache_rhs[test_inpr[i]] = proj_rhs
            else:
                proj_rhs = rhs[i] - np.dot(rhs[i], np.transpose(wr[i])) * wr[i]
            for j in xrange(lhs.shape[0]):
                if cache:
                    key = str(test_inpr[i]) + "-" + str(j)
                    if key in cache_result:
                        result[i][j] = cache_result[key]
                    else:
                        proj_lhs = lhs[j] - np.dot(lhs[j], np.transpose(wr[i])) * wr[i]
                        temp_diff = (proj_lhs + rell[i]) - proj_rhs
                        result[i][j] = -np.sqrt(np.sum(temp_diff**2))
                        cache_result[key] = result[i][j]
                else:
                    proj_lhs = lhs[j] - np.dot(lhs[j], np.transpose(wr[i])) * wr[i]
                    temp_diff = (proj_lhs + rell[i]) - proj_rhs
                    result[i][j] = -np.sqrt(np.sum(temp_diff ** 2))
        return result

    def rank_right_idx(self, test_inpl, test_o, test_w, ent_embs):
        rhs = ent_embs  # [num_entities, d]
        rell = test_o  # [num_test, d]
        lhs = ent_embs[test_inpl]  # [num_test, d]
        wr = test_w
        result = np.zeros((lhs.shape[0], rhs.shape[0]))
        for i in xrange(lhs.shape[0]):
            proj_lhs = lhs[i] - np.dot(lhs[i], np.transpose(wr[i])) * wr[i]
            proj_lhs = proj_lhs + rell[i]
            for j in xrange(rhs.shape[0]):
                proj_rhs = rhs[j] - np.dot(rhs[j], np.transpose(wr[i])) * wr[i]
                temp_diff = proj_lhs - proj_rhs
                result[i][j] = -np.sqrt(np.sum(temp_diff**2))
        return result

    def create_graph(self):
        print('Building Model')
        # Translation Model initialisation
        w_bound = np.sqrt(6. / self.embedding_size)
        self.E = tf.Variable(tf.random_uniform((self.num_entities, self.embedding_size), minval=-w_bound,
                                               maxval=w_bound))
        self.R = tf.Variable(tf.random_uniform((self.num_relations, self.embedding_size), minval=-w_bound,
                                               maxval=w_bound))

        self.W = tf.Variable(tf.random_uniform((self.num_relations, self.embedding_size), minval=-w_bound,
                                               maxval=w_bound))

        self.normalize_W = self.W.assign(tf.nn.l2_normalize(self.W, 1))

        self.inpr = tf.placeholder(tf.int32, [self.batch_size_kg], name="rhs")
        self.inpl = tf.placeholder(tf.int32, [self.batch_size_kg], name="lhs")
        self.inpo = tf.placeholder(tf.int32, [self.batch_size_kg], name="rell")

        self.inprn = tf.placeholder(tf.int32, [self.batch_size_kg], name="rhsn")
        self.inpln = tf.placeholder(tf.int32, [self.batch_size_kg], name="lhsn")
        self.inpon = tf.placeholder(tf.int32, [self.batch_size_kg], name="relln")

        self.test_inpr = tf.placeholder(tf.int32, [None], name="test_rhs")
        self.test_inpl = tf.placeholder(tf.int32, [None], name="test_lhs")
        self.test_inpo = tf.placeholder(tf.int32, [None], name="test_rell")

        lhs = tf.nn.embedding_lookup(self.E, self.inpl)
        rhs = tf.nn.embedding_lookup(self.E, self.inpr)
        rell = tf.nn.embedding_lookup(self.R, self.inpo)
        wr = tf.nn.embedding_lookup(self.W, self.inpo)

        lhsn = tf.nn.embedding_lookup(self.E, self.inpln)
        rhsn = tf.nn.embedding_lookup(self.E, self.inprn)
        relln = tf.nn.embedding_lookup(self.R, self.inpon)
        wrn = tf.nn.embedding_lookup(self.W, self.inpon)

        lhs_proj = lhs - dot(lhs, wr) * wr  # dot and elementwise mul => projection
        rhs_proj = rhs - dot(rhs, wr) * wr

        lhs_proj_n = lhsn - dot(lhsn, wrn) * wrn
        rhs_proj_n = rhsn - dot(rhsn, wrn) * wrn

        simi = l2_similarity(trans(lhs_proj, rell), ident_entity(rhs_proj, rell))
        simin = l2_similarity(trans(lhs_proj_n, relln), ident_entity(rhs_proj_n, relln))

        # TransH Loss
        epsilon = tf.constant(0.0001)
        reg1 = tf.maximum(0., tf.reduce_sum(tf.sqrt(tf.reduce_sum(self.E ** 2, axis=1)) - 1))
        reg2_z = dot(self.W, self.R) ** 2
        reg2_n = tf.expand_dims(tf.sqrt(tf.reduce_sum(self.R ** 2, axis=1)), 1)
        reg2 = tf.reduce_sum(tf.maximum(0., (reg2_z / reg2_n) - epsilon))

        kg_loss = max_margin(simi, simin) + self.lambd * (reg1 + reg2)

        if self.sub_prop_constr:
            sub_relations = tf.nn.embedding_lookup(self.R, self.sub_prop_constr["sub"])
            sup_relations = tf.nn.embedding_lookup(self.R, self.sub_prop_constr["sup"])
            kg_loss += tf.reduce_sum(dot(sub_relations, sup_relations) - 1)

        # Skipgram Model
        self.train_inputs = tf.placeholder(tf.int32, shape=[self.batch_size_sg])
        self.train_labels = tf.placeholder(tf.int32, shape=[self.batch_size_sg, 1])

        sg_embed = tf.nn.embedding_lookup(self.E, self.train_inputs)

        if self.skipgram:
            sg_loss = skipgram_loss(self.vocab_size, self.num_sampled, sg_embed, self.embedding_size, self.train_labels)
            self.loss = kg_loss + sg_loss
        else:
            self.loss = kg_loss
        self.global_step = tf.Variable(0, trainable=False)
        starter_learning_rate = self.init_lr
        learning_rate = tf.constant(starter_learning_rate)
        self.optimizer = tf.train.AdagradOptimizer(learning_rate).minimize(self.loss)

        self.ranking_test_inpo = tf.nn.embedding_lookup(self.R, self.test_inpo)
        self.ranking_test_inpw = tf.nn.embedding_lookup(self.W, self.test_inpo)

    def assign_initial(self, init_embeddings):
        return self.E.assign(init_embeddings)

    def post_ops(self):
        return [self.normalize_W]

    def train(self):
        return [self.optimizer, self.loss]

    def variables(self):
        return [self.E, self.R, self.W]
