from collections import Counter

import torch


def _count_ngram(ngram_input_list: list, n_gram: int) -> Counter:
    """Counting how many times each word appears in a given text with ngram

    Args:
        ngram_input_list: A list of translated text or reference texts
        n_gram: gram value ranged 1 to 4

    Return:
        ngram_counter: a collections.Counter object of ngram
    """

    ngram_counter = Counter()

    for i in range(1, n_gram + 1):
        for j in range(len(ngram_input_list) - i + 1):
            ngram_key = tuple(ngram_input_list[j : i + j])
            ngram_counter[ngram_key] += 1

    return ngram_counter


def bleu_score(translate_corpus: list, reference_corpus: list, n_gram: int = 4, smooth: bool = False) -> torch.Tensor:
    """Calculate BLEU score of machine translated text with one or more references.

    Args:
        translate_corpus: A list of lists of translated texts
        reference_corpus: A list of lists of reference texts
        n_gram: Gram value ranged from 1 to 4 (Default 4)
        smooth: Whether or not to apply smoothing – Lin et al. 2004

    Return:
        A Tensor with BLEU Score

    Example:

        >>> translate_corpus = ["the FAST brown fox jumped over the lazy dog".split(' ')]
        >>> reference_corpus = [["the quick brown fox jumped over the lazy dog".split(' '), "the quick brown fox jumped over the the lazy cat".split(' ')]]
        >>> bleu_score(translate_corpus, reference_corpus)
        tensor(0.7506)
    """

    assert len(translate_corpus) == len(reference_corpus)
    numerator = torch.zeros(n_gram)
    denominator = torch.zeros(n_gram)
    precision_scores = torch.zeros(n_gram)
    c = 0.0
    r = 0.0
    # referenced from https://pytorch.org/text/_modules/torchtext/data/metrics.html#bleu_score
    for (translation, references) in zip(translate_corpus, reference_corpus):
        c += len(translation)
        ref_len_list = [len(ref) for ref in references]
        ref_len_diff = [abs(len(translation) - x) for x in ref_len_list]
        r += ref_len_list[ref_len_diff.index(min(ref_len_diff))]
        translation_counter = _count_ngram(translation, n_gram)
        reference_counter = Counter()
        for ref in references:
            reference_counter |= _count_ngram(ref, n_gram)

        ngram_counter_clip = translation_counter & reference_counter
        for counter_clip in ngram_counter_clip:
            numerator[len(counter_clip) - 1] += ngram_counter_clip[counter_clip]

        for counter in translation_counter:
            denominator[len(counter) - 1] += translation_counter[counter]

    trans_len = torch.tensor(c)
    ref_len = torch.tensor(r)
    if min(numerator) == 0.0:
        return torch.tensor(0.0)

    if smooth:
        precision_scores = torch.add(numerator, torch.ones(n_gram)) / torch.add(denominator, torch.ones(n_gram))
    else:
        precision_scores = numerator / denominator
    log_precision_scores = torch.tensor([1.0 / n_gram] * n_gram) * torch.log(precision_scores)
    geometric_mean = torch.exp(torch.sum(log_precision_scores))
    brevity_penalty = torch.tensor(1.0) if c > r else torch.exp(1 - (ref_len / trans_len))
    bleu = brevity_penalty * geometric_mean

    return bleu
